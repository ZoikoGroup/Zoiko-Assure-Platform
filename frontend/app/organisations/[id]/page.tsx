"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { CirclePause, CirclePlay, Pencil, UserPlus, UserRound } from "lucide-react";
import { api, errText, useApi } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { Alert, Breadcrumbs, Button, DescriptionList, EmptyState, ErrorState, Field, Modal, Skeleton, StateBadge, TextInput, Textarea, Timeline } from "@/components/ds";
import { ClassificationBadge } from "@/components/platformQueue";
import {
  OneTimePassword, OrganisationStatus, RegistryHeading, RegistryScope, RegistrySection, actionRequiredByOrganisation,
  type AdminEvent, type RegistryRecord,
} from "@/components/PlatformAdmin";
import { Notice, PageHeader, useAction, usePageTitle } from "@/components/ui";

/* Super Admin: one customer organisation's registry record (ZA-TEN-001 WF-TEN-01). Metadata only; no customer data.
   Only the platform Super Admin is served GET /platform/organisations/{id}, so, as on the directory (/organisations),
   nobody else's view makes the request: the server would refuse it and record the refusal in that person's own
   organisation's audit trail. The record is read only once the signed-in person is known to be the Super Admin; the
   application shell renders no page before the session is confirmed. */
export default function OrganisationPage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Tenants & Entities" />
        <Notice>Tenants &amp; Entities is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <OrganisationRecord />;
}

type Dialog = "edit" | "status" | "admin" | null;

function OrganisationRecord() {
  const { id } = useParams<{ id: string }>();
  const org = useApi<RegistryRecord>(`/platform/organisations/${id}`);
  const [dialog, setDialog] = useState<Dialog>(null);
  const o = org.data;
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(o?.legal_name ?? "Organisation"); return () => setTitle(""); }, [o?.legal_name, setTitle]);
  const suspended = o?.status === "SUSPENDED";
  // The Action Queue's own rule decides whether a missing Organisation Admin needs action (ACTIVE organisations).
  const needsAdmin = useMemo(() => (o ? actionRequiredByOrganisation([o]).get(o.tenant_id) : undefined), [o]);
  const close = () => setDialog(null);
  const saved = (data: RegistryRecord) => { org.setData(data); };

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "Tenants & Entities", href: "/organisations" }, { label: o?.legal_name ?? "Organisation record" }]} />
      <RegistryHeading kicker="Customer organisation · registry record"
        title={o?.legal_name ?? "Organisation"} badge={o && <OrganisationStatus status={o.status} />}
        actions={o && <>
          <Button variant="secondary" icon={Pencil} onClick={() => setDialog("edit")}>Edit name</Button>
          <Button variant="secondary" icon={UserPlus} onClick={() => setDialog("admin")}>Appoint Organisation Admin</Button>
          {suspended
            ? <Button variant="primary" icon={CirclePlay} onClick={() => setDialog("status")}>Reactivate</Button>
            : <Button variant="danger" icon={CirclePause} onClick={() => setDialog("status")}>Suspend</Button>}
        </>}>
        <p>Status, placement, legal entities and Organisation Admins, administered by the Zoiko platform. This organisation&apos;s regulatory records are not visible here.</p>
      </RegistryHeading>

      {!o && org.error ? (
        <RegistrySection title="Registry record">
          <ErrorState error={org.error} title="This organisation record could not be loaded" onRetry={() => org.reload()} />
          <p className="mt-2 text-center type-caption"><Link href="/organisations" className="font-semibold text-accent-fg hover:underline">Back to Tenants &amp; Entities</Link></p>
        </RegistrySection>
      ) : !o ? <RecordLoading /> : (
        <>
          {suspended && (
            <Alert tone="warning" title="This organisation is suspended.">
              Its people cannot sign in and any earlier sessions were ended. Reactivate it to let them sign in again.
            </Alert>
          )}
          <RegistryScope>
            <span className="font-semibold text-fg">Registry metadata only.</span> Status, placement, legal entities and Organisation Admins.
            This organisation&apos;s evidence, facts and assurance records are not reachable from this role.
          </RegistryScope>

          <RegistrySection title="Registry record" description="Placement (cell, isolation tier and contracting region) is assigned by the server when the organisation is created.">
            <DescriptionList columns={3} items={[
              ["Status", <span key="s" className="inline-flex flex-wrap items-center gap-2"><OrganisationStatus status={o.status} /><code className="type-code text-fg-muted">{o.status}</code></span>],
              ["Organisation ID", <code key="t" className="break-all type-code">{o.tenant_id}</code>],
              ["Contracting region", o.contracting_region],
              ["Isolation tier", <code key="i" className="type-code">{o.isolation_tier}</code>],
              ["Cells", <code key="c" className="type-code">{o.cells.join(", ") || "—"}</code>],
              ["Created", fmtDate(o.created_at, true)],
            ]} />
          </RegistrySection>

          <div className="grid gap-6 xl:grid-cols-2">
            <RegistrySection title="Legal entities" description="Each entity's data stays in its home cell. Further entities are registered by the organisation itself.">
              {o.entities.length === 0 ? <EmptyState compact title="No legal entities recorded" /> : (
                <ul aria-label="Legal entities" className="divide-y divide-line">
                  {o.entities.map((e) => (
                    <li key={e.entity_id} className="py-3 first:pt-0 last:pb-0">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="break-words type-secondary font-semibold text-fg">{e.legal_name}</p>
                          <p className="break-all type-code text-fg-muted">{e.entity_id}</p>
                        </div>
                        <StateBadge code={e.status} size="sm" />
                      </div>
                      <DescriptionList columns={3} className="mt-2" items={[
                        ["Country", <code key="c" className="type-code">{e.country}</code>],
                        ["Registration", e.registration_ref ?? "—"],
                        ["Home cell", <code key="h" className="type-code"><span className="whitespace-nowrap">{e.home_cell_id}</span>{e.region && <> · <span className="whitespace-nowrap">{e.region}</span></>}</code>],
                      ]} />
                    </li>
                  ))}
                </ul>
              )}
            </RegistrySection>

            <RegistrySection title="Organisation Admins" description="Role TENANT_OWNER: administers this organisation only, and cannot attest or approve on its behalf.">
              {o.admins.length === 0 ? (
                <div className="flex flex-col items-start gap-3 rounded-card border border-dashed border-line-strong p-4">
                  {needsAdmin && <ClassificationBadge classification={needsAdmin.classification} />}
                  <div className="min-w-0">
                    <p className="type-secondary font-semibold text-fg">No Organisation Admin appointed</p>
                    <p className="mt-0.5 type-caption text-fg-secondary">
                      {needsAdmin ? needsAdmin.why : "Nobody administers this organisation. Its people cannot sign in while it is suspended."}
                    </p>
                  </div>
                  <Button variant="primary" size="sm" icon={UserPlus} onClick={() => setDialog("admin")}>Appoint Organisation Admin</Button>
                </div>
              ) : (
                <ul aria-label="Organisation Admins" className="divide-y divide-line">
                  {o.admins.map((a) => (
                    <li key={a.actor_id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                      <UserRound size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
                      <div className="min-w-0">
                        <p className="break-words type-secondary font-medium text-fg">{a.display_name}</p>
                        <p className="break-all type-code text-fg-muted">{a.email ?? "No email recorded"}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </RegistrySection>
          </div>

          <RegistrySection title="Administrative history"
            description={`Platform changes to this organisation, as recorded in its audit trail, newest first.${o.admin_events.length >= 50 ? " The registry returns the 50 most recent." : ""}`}>
            {o.admin_events.length === 0 ? <EmptyState compact title="No platform changes recorded" /> : (
              <Timeline label="Administrative history" items={o.admin_events.map((e) => ({
                id: String(e.record_seq),
                title: eventTitle(e),
                meta: <EventMeta e={e} />,
                description: <EventDetail e={e} />,
              }))} />
            )}
          </RegistrySection>
        </>
      )}

      {o && dialog === "edit" && <EditName org={o} onClose={close} onSaved={saved} />}
      {o && dialog === "status" && <ChangeStatus org={o} onClose={close} onSaved={saved} />}
      {o && dialog === "admin" && <AppointAdmin org={o} onClose={close} onSaved={saved} />}
    </div>
  );
}

function RecordLoading() {
  return (
    <div role="status" aria-label="Loading the organisation record" className="space-y-6">
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-panel border border-line bg-surface-raised/70 p-6">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="mt-4 h-4 w-3/4" />
          <Skeleton className="mt-2 h-4 w-1/2" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------- administrative history */
/** Readable names for the audit actions this record lists (routers/platform.py `_detail`). Any other action is shown
 *  as recorded. The recorded code stays visible beside each entry. */
const EVENT_LABEL: Record<string, string> = {
  "organisation.created": "Organisation created",
  "organisation.updated": "Organisation details updated",
  "organisation.admin_appointed": "Organisation Admin appointed",
  "account.bootstrap_created": "Account created at start-up",
};
function eventTitle(e: AdminEvent): string {
  if (e.action === "organisation.status_changed") {
    return e.new_state === "SUSPENDED" ? "Organisation suspended" : e.new_state === "ACTIVE" ? "Organisation reactivated" : "Organisation status changed";
  }
  return EVENT_LABEL[e.action] ?? e.action;
}

/** When, who and the recorded action code. The record carries the actor's ID only (no display name), so the ID is
 *  what is shown. */
function EventMeta({ e }: { e: AdminEvent }) {
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <time dateTime={e.recorded_at}>{fmtDate(e.recorded_at, true)}</time>
      <span aria-hidden>·</span>
      {e.actor_id ? <span>Actor ID <code className="break-all type-code">{e.actor_id}</code></span> : <span>Actor not recorded</span>}
      <span aria-hidden>·</span>
      <code className="type-code">{e.action}</code>
    </span>
  );
}

function EventDetail({ e }: { e: AdminEvent }) {
  if (!e.prior_state && !e.new_state && !e.reason) return null;
  return (
    <div className="space-y-0.5">
      {(e.prior_state || e.new_state) && (
        <p>
          <code className="type-code">{e.prior_state ?? "none"}</code><span aria-hidden> → </span><span className="sr-only"> to </span>
          <code className="type-code">{e.new_state ?? "none"}</code>
        </p>
      )}
      {e.reason && <p className="break-words">{e.reason}</p>}
    </div>
  );
}

/* --------------------------------------------------------------------------------------------------------- dialogs */
type DialogProps = { org: RegistryRecord; onClose: () => void; onSaved: (o: RegistryRecord) => void };

function EditName({ org, onClose, onSaved }: DialogProps) {
  const [name, setName] = useState(org.legal_name);
  const a = useAction();
  const formId = useId();
  const valid = name.trim().length >= 2 && name.trim() !== org.legal_name;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid || a.busy) return;
    void a.run(async () => {
      onSaved(await api<RegistryRecord>(`/platform/organisations/${org.tenant_id}`, { method: "PATCH", json: { legal_name: name.trim() } }));
      onClose();
    });
  };
  return (
    <Modal open onClose={onClose} title="Edit organisation name" size="sm" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button type="submit" form={formId} variant="primary" disabled={!valid} loading={a.busy}>Save</Button>
    </>}>
      <form id={formId} onSubmit={submit} className="space-y-4">
        {!!a.error && <Alert tone="critical" live="assertive">{errText(a.error)}</Alert>}
        <Field label="Organisation legal name" required hint="The change is recorded in the organisation's audit trail.">
          <TextInput value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}

/** Suspend or reactivate. Suspending needs the reason and the organisation's legal name typed exactly; the button stays
 *  disabled until both are given, and the submit handler checks them again, so Enter cannot skip them either. */
function ChangeStatus({ org, onClose, onSaved }: DialogProps) {
  const target = org.status === "SUSPENDED" ? "ACTIVE" : "SUSPENDED";
  const suspending = target === "SUSPENDED";
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const a = useAction();
  const formId = useId();
  const nameMatches = typed.trim() === org.legal_name;
  const valid = reason.trim().length >= 3 && (!suspending || nameMatches);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid || a.busy) return;
    void a.run(async () => {
      onSaved(await api<RegistryRecord>(`/platform/organisations/${org.tenant_id}/status`, { method: "POST", json: { status: target, reason: reason.trim() } }));
      onClose();
    });
  };
  return (
    <Modal open onClose={onClose} title={suspending ? "Suspend organisation" : "Reactivate organisation"} size={suspending ? "md" : "sm"} footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button type="submit" form={formId} variant={suspending ? "danger" : "primary"} disabled={!valid} loading={a.busy}>
        {suspending ? "Suspend organisation" : "Reactivate organisation"}
      </Button>
    </>}>
      <form id={formId} onSubmit={submit} className="space-y-4">
        {!!a.error && <Alert tone="critical" live="assertive">{errText(a.error)}</Alert>}
        {suspending ? (
          <Alert tone="critical" title={`Suspending ${org.legal_name}`}>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              <li>Every active session of its people is revoked now.</li>
              <li>Nobody in the organisation can sign in or use Zoiko Assure until it is reactivated.</li>
              <li>No data is deleted, and its records are not changed.</li>
              <li>The change and your reason are recorded in the organisation&apos;s audit trail.</li>
            </ul>
          </Alert>
        ) : (
          <p>Reactivating sets <b className="text-fg">{org.legal_name}</b> to Active, so its people can sign in again. Sessions that ended when it was suspended stay ended.</p>
        )}
        <Field label="Reason" required hint="Recorded in the audit trail. At least 3 characters.">
          <Textarea rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        {suspending && (
          <Field label="Type the organisation's legal name to confirm" required
            hint={<>Enter <b className="break-words text-fg">{org.legal_name}</b> exactly as shown.</>}
            error={typed.trim() && !nameMatches ? "This does not match the organisation's legal name." : undefined}>
            <TextInput value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
        )}
      </form>
    </Modal>
  );
}

function AppointAdmin({ org, onClose, onSaved }: DialogProps) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const a = useAction();
  const formId = useId();
  const result = a.result as { initial_admin_password?: string } | null;
  const valid = name.trim().length >= 2 && /.+@.+\..+/.test(email.trim());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid || a.busy) return;
    void a.run(async () => {
      const r = await api<RegistryRecord & { initial_admin_password: string }>(`/platform/organisations/${org.tenant_id}/admins`, { method: "POST", json: { display_name: name.trim(), email: email.trim() } });
      const { initial_admin_password: _shownOnce, ...record } = r;  // the page keeps the record, not the password
      onSaved(record);
      return r;
    });
  };
  return (
    <Modal open onClose={onClose} title="Appoint Organisation Admin" footer={result ? <Button variant="primary" onClick={onClose}>Done</Button> : <>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button type="submit" form={formId} variant="primary" disabled={!valid} loading={a.busy}>Appoint</Button>
    </>}>
      {result ? (
        <OneTimePassword email={email.trim().toLowerCase()} password={result.initial_admin_password} />
      ) : (
        <form id={formId} onSubmit={submit} className="space-y-4">
          {!!a.error && <Alert tone="critical" live="assertive">{errText(a.error)}</Alert>}
          <p>The new admin gets role TENANT_OWNER in {org.legal_name} only, with a one-time temporary password to replace at first sign-in.</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" required><TextInput value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Email" required><TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          </div>
        </form>
      )}
    </Modal>
  );
}
