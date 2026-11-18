"use client";

import { useState } from "react";
import { RotateCw } from "lucide-react";
import { errText } from "@/lib/api";
import { useAuth, type Me, type RoleAssignment } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { assignmentSourceText, permissionText, roleAbout, roleLabel, scopeText, workContext } from "@/lib/roles";
import { Alert, Button, DescriptionList, EmptyState, StateBadge } from "../ds";
import { RegistrySection } from "../PlatformAdmin";

/* Users & Roles → Your Access, shared by the Super Admin's tab (components/PlatformUsers.tsx) and everyone else's
   Team & Access / Users & Roles screen (app/admin/users/page.tsx). Everything shown is the session check's own answer
   (GET /auth/session; ZA-IAM-001 §23, WF-IAM-02): the account, each role assignment in force with its scope, validity,
   source and permissions, and the permissions grouped by where they apply. Display only: the backend decides every
   request from the role registry, whatever this page shows. */

type Group = { key: string; title: string; permissions: string[] };

/** The permissions in force now, by where they apply: organisation- or platform-wide (`me.permissions`), then, for each
 *  legal entity an assignment is scoped to, the further permissions that apply only to that entity. */
export function permissionGroups(me: Me): Group[] {
  const wide = new Set(me.permissions);
  const byEntity = new Map<string, Set<string>>();
  for (const a of me.role_assignments) {
    if (!a.in_force || a.scope_type !== "LEGAL_ENTITY" || !a.scope_id) continue;
    const own = byEntity.get(a.scope_id) ?? new Set<string>();
    a.permissions.forEach((p) => { if (!wide.has(p)) own.add(p); });
    byEntity.set(a.scope_id, own);
  }
  const groups: Group[] = [];
  if (wide.size) {
    groups.push({ key: "wide", title: me.tenant_id ? `Across ${me.tenant_name || "your organisation"}` : "Across the Zoiko platform",
                  permissions: [...wide].sort() });
  }
  for (const [entity, perms] of [...byEntity].sort(([a], [b]) => a.localeCompare(b))) {
    if (perms.size) groups.push({ key: `entity:${entity}`, title: `Only for legal entity ${entity}`, permissions: [...perms].sort() });
  }
  return groups;
}

function PermissionList({ permissions, label }: { permissions: string[]; label: string }) {
  return (
    <ul aria-label={label} className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
      {permissions.map((p) => (
        <li key={p} className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-line py-1.5">
          <span className="type-secondary text-fg">{permissionText(p)}</span><code className="type-code text-fg-muted">{p}</code>
        </li>
      ))}
    </ul>
  );
}

function AssignmentCard({ a, me }: { a: RoleAssignment; me: Me }) {
  const where = a.scope_type === "LEGAL_ENTITY" ? ` for legal entity ${a.scope_id}` : "";
  return (
    <li className="rounded-card border border-line bg-surface-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="type-secondary font-semibold text-fg">{roleLabel(a.role_code)} <code className="ml-1 type-code font-normal text-fg-muted">{a.role_code}</code></p>
          {roleAbout(a.role_code) && <p className="mt-0.5 type-caption text-fg-secondary">{roleAbout(a.role_code)}</p>}
        </div>
        <span className="flex shrink-0 flex-wrap items-center gap-2">
          <StateBadge code={a.status} size="sm" />
          {a.status === "ACTIVE" && !a.in_force && <span className="type-caption text-fg-muted">not in force now</span>}
        </span>
      </div>
      <DescriptionList columns={3} className="mt-3" items={[
        ["Scope", scopeText(a, me)],
        ["Valid", <span key="v">{fmtDate(a.valid_from, true)} → {a.valid_to ? fmtDate(a.valid_to, true) : "open-ended"}</span>],
        ["Granted through", <span key="s">{assignmentSourceText(a.source)} <code className="type-code text-fg-muted">{a.source}</code></span>],
      ]} />
      {a.permissions.length === 0 ? <p className="mt-3 type-caption text-fg-muted">Confers no permissions.</p> : (
        <details className="mt-3">
          <summary className="cursor-pointer type-caption font-semibold text-fg-secondary">
            {a.permissions.length === 1 ? "1 permission" : `${a.permissions.length} permissions`}{where}
          </summary>
          <div className="mt-2"><PermissionList permissions={a.permissions} label={`Permissions from ${roleLabel(a.role_code)}${where}`} /></div>
        </details>
      )}
    </li>
  );
}

/** Your account, your role assignments and what they let you do, with a Refresh that repeats the session check.
 *  `onRefreshed`: also reload whatever the page shows beside it (its session list). */
export function YourAccess({ onRefreshed }: { onRefreshed?: () => unknown }) {
  const { me, reloadAccess } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  if (!me) return null;
  const c = workContext(me);
  const groups = permissionGroups(me);

  const refresh = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // A 401 signs out inside reloadAccess; anything else lands here and the current view stays as it was.
      await Promise.all([reloadAccess(), onRefreshed?.()]);
      setRefreshedAt(new Date().toISOString());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <RegistrySection title="Your account" description="Who you are signed in as and where you work. The backend enforces your roles on every request."
        aside={
          <span className="flex flex-wrap items-center gap-3">
            {refreshedAt && !busy && <span className="type-caption text-fg-muted">Updated {fmtDate(refreshedAt, true)}</span>}
            <Button size="sm" variant="secondary" icon={RotateCw} loading={busy} onClick={refresh}>{busy ? "Refreshing…" : "Refresh"}</Button>
          </span>
        }>
        {!!error && (
          <Alert tone="critical" live="assertive" title="Your access could not be refreshed" className="mb-4">
            {errText(error)} You are still signed in; what is shown is from your last successful check.
          </Alert>
        )}
        <DescriptionList columns={3} items={[
          ["Name", me.display_name.replace(/\s*\(.*\)/, "")],
          ["Email", me.email ? <span key="e" className="break-all type-code">{me.email}</span> : "No email recorded"],
          ["Workspace", c.kind === "organisation"
            ? <span key="w">{c.name}<code className="block break-all type-code text-fg-muted">{c.id}</code></span>
            : <span key="w">{c.label} · {c.name}<span className="block type-caption text-fg-muted">Not a member of any customer organisation</span></span>],
          ["Authority basis", me.authority_basis ?? "—"],
          ["Principal type", <code key="t" className="type-code">{me.actor_type}</code>],
          ["Account ID", <code key="i" className="break-all type-code">{me.actor_id}</code>],
        ]} />
      </RegistrySection>

      <RegistrySection title="Roles and assignments"
        description="Each role you hold now: where it applies, for how long, how it was granted and the permissions it confers there, as the role registry records it.">
        {me.role_assignments.length === 0 ? <EmptyState compact title="No roles in force" /> : (
          <ul aria-label="Your role assignments" className="space-y-3">
            {me.role_assignments.map((a) => <AssignmentCard key={a.assignment_id} a={a} me={me} />)}
          </ul>
        )}
      </RegistrySection>

      <RegistrySection title="What you can do"
        description="The permissions your roles confer now, by where they apply. Shown for your information: the server checks every request.">
        {groups.length === 0 ? <EmptyState compact title="No permissions" /> : (
          <div className="space-y-5">
            {groups.map((g) => (
              <div key={g.key}>
                <h3 className="mb-2 type-caption font-semibold text-fg-secondary">{g.title}</h3>
                <PermissionList permissions={g.permissions} label={g.title} />
              </div>
            ))}
          </div>
        )}
      </RegistrySection>
    </div>
  );
}
