"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import SCREEN_ACCESS from "@/lib/screen-access.json";
import { permissionText, ROLE_CODES, roleAbout, roleLabel, workContext } from "@/lib/roles";
import { Badge, Card, Drawer, ErrorBox, Field, KV, Modal, Notice, PageHeader, SearchInput, Select, Table, useAction, useToast } from "@/components/ui";
import { PlatformUsers } from "@/components/PlatformUsers";

/* Team & Access (WF-IAM-01 Identity & Access Directory, WF-IAM-02 Effective Access; ZA-IAM-001 §08, §23).
   Everyone sees their own access and sessions. The directory and the role registry appear only for the roles the
   backend serves them to (lib/screen-access.json `panels`, generated from the route guards); every change is decided,
   recorded and audited by the server, which also refuses self-assignment and anything outside the organisation. */
const PANELS = SCREEN_ACCESS.panels;
const holds = (perms: string[], mine?: readonly string[]) => perms.some((p) => mine?.includes(p));

type Session = { session_id: string; created_at: string; expires_at: string; user_agent: string | null; current: boolean };
type Entity = { entity_id: string; legal_name: string; country: string };
type Catalogue = {
  policy_version: string; tenant_id: string; entities: Entity[];
  roles: { role_code: string; permissions: string[]; assignable: boolean; assigned_by_permission: string | null; principal_types: string[]; scope_types: string[] }[];
};
const PRINCIPAL_TYPES = [{ value: "", label: "All principal types" }, ...["HUMAN", "EXTERNAL_AUDITOR", "AGENT", "SERVICE"].map((t) => ({ value: t, label: t }))];

export default function UsersAndRoles() {
  const { me } = useAuth();
  // The Zoiko Super Admin has its own Users & Roles (components/PlatformUsers.tsx); everyone else keeps this view.
  if (me && personaOf(me) === "platform") return <PlatformUsers />;
  const tenant = personaOf(me) === "tenant";
  return (
    <>
      {/* Organisation users' sidebar calls this screen Team & Access; content staff's calls it Users & Roles. */}
      <PageHeader spec="WF-IAM-01/02 · ZA-IAM-001 §08 · §23" title={tenant ? "Team & Access" : "Users & Roles"}
        subtitle={tenant
          ? `Your own access and sessions, and everyone in ${me?.tenant_name ?? "your organisation"} with their roles.`
          : "Your own access and sessions, then the Zoiko staff accounts. Zoiko staff belong to no customer organisation."} />
      <div className="space-y-4">
        <YourAccess />
        <YourSessions />
        {holds(PANELS.directory.perms, me?.permissions) && <Directory />}
        <Notice>
          {tenant
            ? "Organisation Admins are appointed by the Zoiko Super Admin; security administrators by an Organisation Admin; every other role by a security administrator, at organisation or legal-entity scope. Adding new people (invitations, enterprise sign-in and SCIM provisioning, ZA-IAM-001 §03–§04) is not available yet."
            : "Zoiko platform roles are granted only by the one-time server bootstrap, never through this screen (WF-IAM-01: no “make super admin” button)."}
        </Notice>
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
              {permissionText(p)}<span className="mono shrink-0 text-[11px] text-slate-500">{p}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function SessionTable({ rows, loading, onRevoke }: { rows: Session[] | null; loading: boolean; onRevoke: (s: Session) => void }) {
  return (
    <Table head={["Signed in", "Expires", "Device", ""]} empty={rows?.length === 0} loading={loading}>
      {rows?.map((s) => (
        <tr key={s.session_id}>
          <td className="td text-xs">{fmtDate(s.created_at, true)}</td>
          <td className="td text-xs">{fmtDate(s.expires_at, true)}</td>
          <td className="td max-w-[280px] truncate text-xs text-slate-600" title={s.user_agent ?? undefined}>{s.user_agent || "—"}</td>
          <td className="td text-right">
            {s.current ? <Badge code="CURRENT" label="This session" tone="info" />
              : <button type="button" className="btn-ghost" onClick={() => onRevoke(s)}>End session</button>}
          </td>
        </tr>
      ))}
    </Table>
  );
}

function YourSessions() {
  const { data, error, loading, reload } = useApi<Session[]>("/sessions");
  const [target, setTarget] = useState<Session | null>(null);
  return (
    <Card title="Your sessions" subtitle="Every place you are signed in. End any session you do not recognise; to end this one, sign out.">
      <ErrorBox error={error} />
      <SessionTable rows={data} loading={loading && !data} onRevoke={setTarget} />
      {target && <EndSession session={target} own onClose={() => setTarget(null)} onDone={reload} />}
    </Card>
  );
}

/** ZA-IAM-001 §23 POST /sessions/{id}/revoke: the session's own principal, or the organisation's security administrator
 *  (who must give a reason). The session stops working at its next request. */
function EndSession({ session, own, onClose, onDone }: { session: Session; own: boolean; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const a = useAction();
  const toast = useToast();
  const ok = own || reason.trim().length >= 3;
  return (
    <Modal open onClose={onClose} title="End session" size="sm">
      <ErrorBox error={a.error} />
      <p className="mb-3 text-[13px] text-slate-600">The session signed in {fmtDate(session.created_at, true)} stops working immediately. {own ? "" : "The person is not told why; the reason is recorded in the audit trail."}</p>
      <Field label={own ? "Reason (optional)" : "Reason (required, recorded in the audit trail)"} htmlFor="end-session-reason">
        <textarea id="end-session-reason" className="input" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-danger" disabled={!ok || a.busy} onClick={() => a.run(async () => {
          await api(`/sessions/${encodeURIComponent(session.session_id)}/revoke`, { method: "POST", json: reason.trim() ? { reason: reason.trim() } : {} });
          toast("Session ended", "good");
          onDone();
          onClose();
        })}>{a.busy ? "Ending…" : "End session"}</button>
      </div>
    </Modal>
  );
}

function Directory() {
  const { me } = useAuth();
  const tenant = personaOf(me) === "tenant";
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [role, setRole] = useState("");
  const qs = new URLSearchParams();
  if (q) qs.set("q", q);
  if (type) qs.set("actor_type", type);
  if (role) qs.set("role", role);
  const query = qs.toString();
  const { data, error, loading, reload } = useApi<any[]>(`/actors${query ? `?${query}` : ""}`);
  const manage = tenant && holds(PANELS.role_registry.perms, me?.permissions);
  const [open, setOpen] = useState<any | null>(null);
  const filtered = !!(q || type || role);
  return (
    <Card title={tenant ? `People and agents in ${me?.tenant_name ?? "your organisation"}` : "Zoiko staff accounts"}
      subtitle="Only HUMAN and EXTERNAL_AUDITOR actors can sign in. Agents and services act only through the authority granted to them.">
      <form className="mb-3 flex flex-wrap items-end gap-2" role="search" aria-label="Filter the directory"
        onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }}>
        <SearchInput aria-label="Name or email" placeholder="Name or email" maxLength={100} value={text} onChange={(e) => setText(e.target.value)}
          className="min-w-[220px] flex-1" />
        <Select aria-label="Principal type" value={type} onChange={(e) => setType(e.target.value)} options={PRINCIPAL_TYPES} />
        <Select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}
          options={[{ value: "", label: "Any role" }, ...ROLE_CODES.map((r) => ({ value: r, label: roleLabel(r) }))]} />
        <button type="submit" className="btn-ghost">Search</button>
        {filtered && <button type="button" className="btn-ghost" onClick={() => { setText(""); setQ(""); setType(""); setRole(""); }}>Clear</button>}
      </form>
      <ErrorBox error={error} />
      <Table head={["Actor", "Type", "Roles", "Authority basis", ...(manage ? [""] : [])]} empty={data?.length === 0} loading={loading && !data}>
        {data?.map((a) => (
          <tr key={a.actor_id}>
            <td className="td"><div className="font-medium text-slate-900">{a.display_name}</div><div className="mono text-[11px] text-slate-500">{a.email ?? a.actor_id}</div></td>
            <td className="td"><Badge code={a.actor_type} tone="info" /></td>
            <td className="td">{(a.roles || []).map((r: string) => <div key={r} className="text-[13px] text-slate-800">{roleLabel(r)} <span className="mono text-[11px] text-slate-500">{r}</span></div>)}</td>
            <td className="td text-xs text-slate-600">{a.authority_basis ?? "—"}</td>
            {manage && <td className="td text-right"><button type="button" className="btn-ghost" onClick={() => setOpen(a)}>Access</button></td>}
          </tr>
        ))}
      </Table>
      {filtered && data && <p className="mt-2 text-[12px] text-slate-500">{data.length} matching {data.length === 1 ? "principal" : "principals"}. The role filter counts roles in force now at any scope.</p>}
      {open && <Principal actor={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </Card>
  );
}

/** WF-IAM-01 principal drawer + WF-IAM-02 effective access: assignments with their scope and validity, the permissions
 *  they confer now, assign / revoke, and (security administrators) the principal's live sessions. */
function Principal({ actor, onClose, onChanged }: { actor: any; onClose: () => void; onChanged: () => void }) {
  const { me } = useAuth();
  const id = encodeURIComponent(actor.actor_id);
  const catalogue = useApi<Catalogue>("/roles");
  const assignments = useApi<any[]>(`/role-assignments?principal_id=${id}&include_closed=true`);
  const access = useApi<any>(`/principals/${id}/effective-access`);
  const sessionsAllowed = holds(PANELS.principal_sessions.perms, me?.permissions);
  const sessions = useApi<Session[]>(sessionsAllowed ? `/principals/${id}/sessions` : null);
  const [assign, setAssign] = useState(false);
  const [revoke, setRevoke] = useState<any | null>(null);
  const [end, setEnd] = useState<Session | null>(null);
  const self = actor.actor_id === me?.actor_id;
  const entityName = (e: string) => catalogue.data?.entities.find((x) => x.entity_id === e)?.legal_name ?? e;
  const scopeText = (a: any) => a.scope_type === "LEGAL_ENTITY" ? `Legal entity · ${entityName(a.scope_id)}` : "Whole organisation";
  const refresh = () => { assignments.reload(); access.reload(); onChanged(); };
  const assignable = (catalogue.data?.roles ?? []).filter((r) => r.assignable && r.assigned_by_permission
    && me?.permissions.includes(r.assigned_by_permission) && r.principal_types.includes(actor.actor_type));
  return (
    <Drawer open onClose={onClose} title={actor.display_name}>
      <div className="space-y-5">
        <KV cols={2} items={[["Email", <span key="e" className="mono text-xs">{actor.email ?? "—"}</span>], ["Principal type", actor.actor_type],
          ["Principal ID", <span key="i" className="mono text-xs">{actor.actor_id}</span>], ["Authority basis", actor.authority_basis ?? "—"]]} />

        <section aria-labelledby="pa-access">
          <h3 id="pa-access" className="mb-2 text-[13px] font-semibold text-slate-900">Effective access now</h3>
          <ErrorBox error={access.error} />
          {access.data && (
            <>
              <div className="label">Organisation-wide</div>
              {access.data.tenant_permissions.length === 0 ? <p className="text-[13px] text-slate-500">None.</p> : (
                <ul className="mb-2 grid gap-x-4 sm:grid-cols-2">{access.data.tenant_permissions.map((p: string) => <li key={p} className="text-[12px] text-slate-700">{permissionText(p)}</li>)}</ul>
              )}
              {Object.entries(access.data.entity_permissions as Record<string, string[]>).map(([e, perms]) => (
                <div key={e} className="mt-2"><div className="label">{entityName(e)}</div>
                  <ul className="grid gap-x-4 sm:grid-cols-2">{perms.map((p) => <li key={p} className="text-[12px] text-slate-700">{permissionText(p)}</li>)}</ul></div>
              ))}
              <p className="mt-1 text-[11px] text-slate-500">Policy {access.data.policy_version}, evaluated {fmtDate(access.data.evaluated_at, true)}.</p>
            </>
          )}
        </section>

        <section aria-labelledby="pa-roles">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 id="pa-roles" className="text-[13px] font-semibold text-slate-900">Role assignments</h3>
            <button type="button" className="btn-primary" disabled={self || assignable.length === 0}
              title={self ? "Nobody can assign a role to themselves (IAM-REQ-021)" : assignable.length === 0 ? "None of your roles can assign a role to this principal" : undefined}
              onClick={() => setAssign(true)}>Assign role</button>
          </div>
          <ErrorBox error={assignments.error ?? catalogue.error} />
          <Table head={["Role", "Scope", "Valid", "Status", ""]} empty={assignments.data?.length === 0} loading={assignments.loading && !assignments.data}>
            {assignments.data?.map((a) => (
              <tr key={a.assignment_id} className="align-top">
                <td className="td text-[13px]">{roleLabel(a.role_code)}<div className="mono text-[11px] text-slate-500">{a.role_code}</div></td>
                <td className="td text-xs">{scopeText(a)}</td>
                <td className="td text-xs">{fmtDate(a.valid_from, true)} → {a.valid_to ? fmtDate(a.valid_to, true) : "open"}</td>
                <td className="td"><Badge code={a.status} />{a.status === "ACTIVE" && !a.in_force && <div className="text-[11px] text-slate-500">not in force now</div>}
                  {a.revoke_reason && <div className="mt-1 text-[11px] text-slate-500">{a.revoke_reason}</div>}</td>
                <td className="td text-right">{a.status === "ACTIVE" && !self && <button type="button" className="btn-ghost" onClick={() => setRevoke(a)}>Revoke</button>}</td>
              </tr>
            ))}
          </Table>
        </section>

        {sessionsAllowed && (
          <section aria-labelledby="pa-sessions">
            <h3 id="pa-sessions" className="mb-2 text-[13px] font-semibold text-slate-900">Active sessions</h3>
            <ErrorBox error={sessions.error} />
            <SessionTable rows={sessions.data} loading={sessions.loading && !sessions.data} onRevoke={setEnd} />
          </section>
        )}
      </div>
      {assign && catalogue.data && <AssignRole actor={actor} catalogue={catalogue.data} roles={assignable} onClose={() => setAssign(false)} onDone={refresh} />}
      {revoke && <RevokeRole assignment={revoke} onClose={() => setRevoke(null)} onDone={refresh} />}
      {end && <EndSession session={end} own={false} onClose={() => setEnd(null)} onDone={() => sessions.reload()} />}
    </Drawer>
  );
}

function AssignRole({ actor, catalogue, roles, onClose, onDone }: { actor: any; catalogue: Catalogue; roles: Catalogue["roles"]; onClose: () => void; onDone: () => void }) {
  const [role, setRole] = useState(roles[0]?.role_code ?? "");
  const rule = roles.find((r) => r.role_code === role);
  const [scope, setScope] = useState("TENANT");
  const [entity, setEntity] = useState(catalogue.entities[0]?.entity_id ?? "");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const a = useAction();
  const toast = useToast();
  const scopes = rule?.scope_types ?? ["TENANT"];
  const effectiveScope = scopes.includes(scope) ? scope : scopes[0];
  const ready = !!rule && reason.trim().length >= 3 && (effectiveScope !== "LEGAL_ENTITY" || !!entity) && (!from || !to || new Date(to) > new Date(from));
  return (
    <Modal open onClose={onClose} title={`Assign a role to ${actor.display_name}`}>
      <ErrorBox error={a.error} />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Role" htmlFor="assign-role" className="sm:col-span-2" hint={rule ? `Grants: ${rule.permissions.map(permissionText).join("; ")}` : undefined}>
          <Select id="assign-role" value={role} onChange={(e) => setRole(e.target.value)} options={roles.map((r) => ({ value: r.role_code, label: `${roleLabel(r.role_code)} (${r.role_code})` }))} />
        </Field>
        <Field label="Scope" htmlFor="assign-scope">
          <Select id="assign-scope" value={effectiveScope} onChange={(e) => setScope(e.target.value)}
            options={scopes.map((s) => ({ value: s, label: s === "TENANT" ? "Whole organisation" : "One legal entity" }))} />
        </Field>
        {effectiveScope === "LEGAL_ENTITY" && (
          <Field label="Legal entity" htmlFor="assign-entity">
            <Select id="assign-entity" value={entity} onChange={(e) => setEntity(e.target.value)}
              options={catalogue.entities.map((x) => ({ value: x.entity_id, label: `${x.legal_name} (${x.country})` }))} />
          </Field>
        )}
        <Field label="Valid from (optional; default now)" htmlFor="assign-from"><input id="assign-from" type="datetime-local" className="input" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="Valid until (optional)" htmlFor="assign-to"><input id="assign-to" type="datetime-local" className="input" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        <Field label="Reason (required, recorded in the audit trail)" htmlFor="assign-reason" className="sm:col-span-2">
          <textarea id="assign-reason" className="input" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!ready || a.busy} onClick={() => a.run(async () => {
          await api("/role-assignments", { method: "POST", json: {
            principal_id: actor.actor_id, role_code: role, scope_type: effectiveScope,
            scope_id: effectiveScope === "LEGAL_ENTITY" ? entity : null,
            valid_from: from ? new Date(from).toISOString() : null, valid_to: to ? new Date(to).toISOString() : null,
            reason: reason.trim(),
          } });
          toast(`${roleLabel(role)} assigned`, "good");
          onDone();
          onClose();
        })}>{a.busy ? "Assigning…" : "Assign"}</button>
      </div>
    </Modal>
  );
}

function RevokeRole({ assignment, onClose, onDone }: { assignment: any; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const a = useAction();
  const toast = useToast();
  return (
    <Modal open onClose={onClose} title={`Revoke ${roleLabel(assignment.role_code)}`} size="sm">
      <ErrorBox error={a.error} />
      <p className="mb-3 text-[13px] text-slate-600">The assignment is closed now; its history remains. Actions it already authorised stand, and any still committing are refused.</p>
      <Field label="Reason (required, recorded in the audit trail)" htmlFor="revoke-reason">
        <textarea id="revoke-reason" className="input" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-danger" disabled={reason.trim().length < 3 || a.busy} onClick={() => a.run(async () => {
          await api(`/role-assignments/${encodeURIComponent(assignment.assignment_id)}/revoke`, { method: "POST", json: { reason: reason.trim() } });
          toast("Role revoked", "good");
          onDone();
          onClose();
        })}>{a.busy ? "Revoking…" : "Revoke"}</button>
      </div>
    </Modal>
  );
}
