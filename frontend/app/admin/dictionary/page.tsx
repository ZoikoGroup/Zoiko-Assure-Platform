"use client";

import { useApi } from "@/lib/api";
import { Badge, Card, ErrorBox, Json, PageHeader } from "@/components/ui";

export default function ControlledDictionary() {
  return (
    <>
      <PageHeader spec="ZA-GLS-001" title="Controlled Dictionary"
        subtitle="The controlled state vocabulary every Assure screen uses: separate state dimensions plus workflow vocabularies and reason codes." />
      <Dictionary />
    </>
  );
}

function Dictionary() {
  const { data, error } = useApi<any>("/dictionary");
  if (!data) return <ErrorBox error={error} />;
  const dims = data.dimensions || {};
  return (
    <div className="space-y-4">
      <Card title={`Dictionary ${data.dictionary_version}`} subtitle="Six separate state dimensions plus workflow vocabularies. There is deliberately no COMPLIANT state.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(dims).map(([dim, vals]) => (
            <div key={dim} className="rounded-lg border border-navy-100 p-3">
              <div className="mono mb-2 text-xs font-semibold text-navy-800">{dim}</div>
              <ul className="space-y-1">
                {Object.entries(vals as Record<string, string>).map(([code, def]) => (
                  <li key={code} className="text-[11px]"><Badge code={code} /> <span className="text-slate-600">{def}</span></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
      {data.reason_codes && <Card title="Reason codes"><Json value={{ reason_codes: data.reason_codes, ipe_reason_codes: data.ipe_reason_codes }} /></Card>}
    </div>
  );
}
