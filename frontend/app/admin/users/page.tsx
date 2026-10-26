"use client";

import { useApi } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { permissionText, roleAbout, roleLabel, workContext } from "@/lib/roles";
import { Badge, Card, ErrorBox, KV, Notice, PageHeader, Table } from "@/components/ui";

export default function UsersAndRoles() {
  const { me } = useAuth();
  const tenant = personaOf(me) === "tenant";
  return (
    <>
      <PageHeader spec="WF-IAM-01/02 · ZA-IAM-001" title="Users & Roles"
        subtitle={tenant
          ? `Your own access, then everyone in ${me?.tenant_name ?? "your organisation"}: people, external auditors, agents and services, with their roles and authority basis.`
          : "Your own access, then the Zoiko staff accounts. Zoiko staff belong to no customer organisation."} />
      <div className="space-y-4">
        <YourAccess />
        <Directory />
        <Notice>Self-service provisioning and role assignment are not yet available. Organisation Admins are appointed by the Zoiko Super Admin, Super Admin accounts are created only by the one-time server bootstrap, and enterprise lifecycle provisioning is planned through SCIM (ZA-IAM-001 §04).</Notice>
      </div>
    </>
  );
}

function YourAccess() {
  const { me } = useAuth();
  if (!me) return null;
  const c = workContext(me);
  return (
    <Card title="Your access" subtitle="Who you are signed in as, where you work, and what your roles let you do. The backend enforces these on every request.">
      <KV cols={4} items={[
        ["Name", me.display_name.replace(/\s*\(.*\)/, "")],
        ["Email", <span key="e" className="mono text-xs">{me.email}</span>],
        [c.kind === "organisation" ? "Organisation" : "Workspace", c.kind === "organisation"
          ? <span key="o">{c.name}<span className="mono block text-[11px] text-slate-500">{c.id}</span></span>
          : <span key="o">{c.label} · {c.name}<span className="block text-[11px] text-slate-500">Not a member of any customer organisation</span></span>],
        ["Authority basis", me.authority_basis ?? "—"],
      ]} />
      <div className="label mt-4">{me.roles.length === 1 ? "Role" : "Roles"}</div>
      <ul className="space-y-1.5">
        {me.roles.map((r) => (
          <li key={r} className="text-[13px]">
            <span className="font-medium text-slate-900">{roleLabel(r)}</span> <span className="mono text-[11px] text-slate-500">{r}</span>
            {roleAbout(r) && <p className="text-xs text-slate-600">{roleAbout(r)}</p>}
          </li>
        ))}
      </ul>
      <div className="label mt-4">What you can do</div>
      {me.permissions.length === 0 ? <p className="text-[13px] text-slate-500">No permissions.</p> : (
        <ul className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          {me.permissions.map((p) => (
            <li key={p} className="flex items-baseline justify-between gap-2 border-b border-slate-100 py-1 text-[13px] text-slate-700">
              {permissionText(p)}<span className="mono shrink-0 text-[10px] text-slate-400">{p}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Directory() {
  const { me } = useAuth();
  const { data, error } = useApi<any[]>("/actors");
  const tenant = personaOf(me) === "tenant";
  return (
    <Card title={tenant ? `People and agents in ${me?.tenant_name ?? "your organisation"}` : "Zoiko staff accounts"}
      subtitle="Only HUMAN and EXTERNAL_AUDITOR actors can sign in. Agents and services act only through the authority granted to them.">
      <ErrorBox error={error} />
      <Table head={["Actor", "Type", "Roles", "Authority basis"]} empty={data?.length === 0}>
        {data?.map((a) => (
          <tr key={a.actor_id}>
            <td className="td"><div className="font-medium text-slate-900">{a.display_name}</div><div className="mono text-[11px] text-slate-500">{a.email ?? a.actor_id}</div></td>
            <td className="td"><Badge code={a.actor_type} tone="info" /></td>
            <td className="td">{(a.roles || []).map((r: string) => <div key={r} className="text-[13px] text-slate-800">{roleLabel(r)} <span className="mono text-[10px] text-slate-500">{r}</span></div>)}</td>
            <td className="td text-xs text-slate-600">{a.authority_basis ?? "—"}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
