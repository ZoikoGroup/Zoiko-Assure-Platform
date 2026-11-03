"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, Hash, Json, PageHeader, Table, useAction } from "@/components/ui";

export default function ContentReleases() {
  return (
    <>
      <PageHeader spec="ZA-ARCH-001 §7 · ZA-RUL-001" title="Content Releases"
        subtitle="Signed regulatory content releases. Each manifest is canonicalised, hashed and Ed25519-signed, and can be verified independently of the database." />
      <Releases />
    </>
  );
}

function Releases() {
  const { data, error } = useApi<any[]>("/catalog/releases");
  const a = useAction();
  const [ver, setVer] = useState<Record<string, any>>({});
  return (
    <Card title="Signed content releases" subtitle="Each release manifest is canonicalised, hashed and Ed25519-signed; verification is independent of the database.">
      <ErrorBox error={error || a.error} />
      <Table head={["Label", "Published", "Manifest hash", "Signer", "Impact", "Verification"]} empty={data?.length === 0} loading={!data && !error}>
        {data?.map((r) => (
          <tr key={r.content_release_id} className="align-top">
            <td className="td"><div className="font-medium">{r.label}</div><div className="text-[11px] text-slate-500">{r.changelog}</div>
              {r.correction_of_release_id && <Badge code="CORRECTION" />}</td>
            <td className="td text-xs">{fmtDate(r.published_at, true)}<div className="text-slate-500">by {r.released_by}</div></td>
            <td className="td"><Hash value={r.manifest_hash} /></td>
            <td className="td mono text-xs">{r.signer_key_id}</td>
            <td className="td text-xs">{r.tenant_impacted_evaluations !== undefined ? `${r.tenant_impacted_evaluations} of your evaluations` : "aggregate only"}</td>
            <td className="td">
              {ver[r.content_release_id] ? (
                <div className="flex gap-1"><Badge code={ver[r.content_release_id].manifest_hash_ok ? "PASS" : "FAIL"} label="hash" /><Badge code={ver[r.content_release_id].signature_ok ? "PASS" : "FAIL"} label="signature" /></div>
              ) : (
                <button className="btn-ghost" onClick={() => a.run(async () => setVer({ ...ver, [r.content_release_id]: await api(`/catalog/releases/${r.content_release_id}/verify`) }))}>Verify</button>
              )}
            </td>
          </tr>
        ))}
      </Table>
      {data?.[0] && <details className="mt-4"><summary className="cursor-pointer text-sm font-medium text-navy-700">Latest manifest</summary><div className="mt-2"><Json value={data[0].manifest} /></div></details>}
    </Card>
  );
}
