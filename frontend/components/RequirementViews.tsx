"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, KV, LoadingSkeleton, Modal, Notice, Table, useAction } from "./ui";

/** Requirement versions across instruments (GET /catalog/instruments). */
export function RequirementList({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error } = useApi<any[]>("/catalog/instruments");
  const reqs = (data || []).flatMap((i) => i.requirements.map((r: any) => ({ ...r, instrument: i.identifier })));
  return (
    <Card title="Requirement versions" subtitle="AI-proposed requirements stay REVIEW_REQUIRED until a qualified human legal reviewer verifies them (INV-09)." bodyClassName="">
      <ErrorBox error={error} />
      {!data && !error ? <LoadingSkeleton rows={4} /> : (
        <Table head={["Requirement", "Instrument", "Origin", "Lifecycle", "Valid from", ""]} empty={reqs.length === 0}>
          {reqs.map((r) => (
            <tr key={r.requirement_version_id}>
              <td className="td"><div className="font-medium text-slate-900">{r.title}</div><div className="mono text-[11px] text-slate-500">{r.requirement_key} · {r.locator}</div></td>
              <td className="td mono text-xs">{r.instrument}</td>
              <td className="td"><Badge code={r.origin} /></td>
              <td className="td"><Badge code={r.lifecycle} /></td>
              <td className="td whitespace-nowrap">{fmtDate(r.valid_from)}</td>
              <td className="td text-right"><button className="btn-ghost" onClick={() => onOpen(r.requirement_version_id)}>Review</button></td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

/** Review one requirement version: provisions, human encoding and legal verification (GET/POST /catalog/requirements/{id}). */
export function RequirementReview({ id, onClose }: { id: string; onClose?: () => void }) {
  const { data, error, reload } = useApi<any>(`/catalog/requirements/${id}`);
  const a = useAction();
  const [edit, setEdit] = useState<any>(null);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-slate-900">{data ? data.title : "Requirement"}</h3>
        <div className="flex gap-2">
          {data?.lifecycle === "REVIEW_REQUIRED" && <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { await api(`/catalog/requirements/${id}/verify`, { method: "POST" }); reload(); })}>Verify (legal reviewer)</button>}
          {onClose && <button className="btn-ghost" onClick={onClose}>Close</button>}
        </div>
      </div>
      <ErrorBox error={error || a.error} />
      {!data && !error && <LoadingSkeleton rows={4} className="p-0" />}
      {data && (
        <>
          <KV cols={4} items={[["Key", <span key="k" className="mono">{data.requirement_key}</span>], ["Lifecycle", <Badge key="l" code={data.lifecycle} />],
            ["Origin", <Badge key="o" code={data.origin} />], ["Reviewed by", data.reviewed_by]]} />
          {data.source?.display_note && <Notice tone="warn">{data.source.display_note}</Notice>}
          <div className="card">
            <Table head={["Locator", "Duty bearer", "Modality", "Action", "Object", "Origin", "State", ""]} empty={!data.provisions.length}>
              {data.provisions.map((p: any) => (
                <tr key={p.provision_id}>
                  <td className="td mono text-xs">{p.locator}</td>
                  <td className="td">{p.duty_bearer === "TBD_BY_REVIEWER" ? <Badge code="MISSING" label="TBD" /> : p.duty_bearer}</td>
                  <td className="td mono text-xs">{p.modality}</td>
                  <td className="td max-w-xs text-xs">{p.action}</td>
                  <td className="td text-xs">{p.regulated_object}</td>
                  <td className="td"><Badge code={p.semantic_origin} /></td>
                  <td className="td"><Badge code={p.lifecycle} /></td>
                  <td className="td">{!["VERIFIED", "PUBLISHED"].includes(p.lifecycle) && <button className="btn-ghost" onClick={() => setEdit({ ...p })}>Encode</button>}</td>
                </tr>
              ))}
            </Table>
          </div>
        </>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title="Encode provision (human structured meaning)">
        {edit && (
          <>
            <p className="mb-3 rounded-md bg-slate-50 p-2 text-xs text-slate-700">{edit.normalized_meaning}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {["duty_bearer", "modality", "action", "regulated_object", "timing", "evidence_expectation"].map((k) => (
                <div key={k}><label className="label" htmlFor={`enc-${k}`}>{k.replace(/_/g, " ")}</label><input id={`enc-${k}`} className="input" value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></div>
              ))}
              <div className="sm:col-span-2"><label className="label" htmlFor="enc-meaning">Normalized meaning</label><textarea id="enc-meaning" className="input" rows={3} value={edit.normalized_meaning} onChange={(e) => setEdit({ ...edit, normalized_meaning: e.target.value })} /></div>
            </div>
            <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary" onClick={() => a.run(async () => {
              const { duty_bearer, modality, action, regulated_object, timing, normalized_meaning, evidence_expectation } = edit;
              await api(`/catalog/provisions/${edit.provision_id}`, { method: "PUT", json: { duty_bearer, modality, action, regulated_object, timing, normalized_meaning, evidence_expectation } });
              setEdit(null); reload();
            })}>Save encoding</button></div>
          </>
        )}
      </Modal>
    </div>
  );
}
