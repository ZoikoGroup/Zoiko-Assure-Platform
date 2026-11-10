"use client";

import { useId, useState } from "react";
import { useApi } from "@/lib/api";
import { Badge, Card, CoverageBadge, Drawer, ErrorBox, Hash, PageHeader, Table, TabPanel, Tabs } from "@/components/ui";
import { RequirementList, RequirementReview } from "@/components/RequirementViews";

export default function Sources() {
  const [tab, setTab] = useState("Requirements & provisions");
  const [open, setOpen] = useState<string | null>(null);
  const tabsId = useId();
  return (
    <>
      <PageHeader spec="WF-DOM-01 · ZA-DOM-001 §4 · ZA-RUL-001" title="Sources & Provisions"
        subtitle="Regulatory instruments, their requirements and catalogue coverage."
        help="Instruments, their atomic requirement versions and provisions, and the catalogue coverage built on them. Extraction stays a non-binding proposal until a qualified legal reviewer encodes and verifies it." />
      <Tabs id={tabsId} tabs={["Requirements & provisions", "Catalogue & coverage"]} value={tab} onChange={setTab} />
      <TabPanel id={tabsId} value={tab}>
        {tab === "Requirements & provisions" && <RequirementList onOpen={setOpen} />}
        {tab === "Catalogue & coverage" && <Catalogue />}
      </TabPanel>
      <Drawer open={!!open} onClose={() => setOpen(null)} title="Requirement review" width="max-w-4xl">
        {open && <RequirementReview id={open} />}
      </Drawer>
    </>
  );
}

function Catalogue() {
  const { data, error } = useApi<any[]>("/catalog/instruments");
  const { data: od } = useApi<any[]>("/catalog/obligation-definitions");
  const { data: fds } = useApi<any[]>("/catalog/fact-definitions");
  const { data: ctl } = useApi<any[]>("/catalog/controls");
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Instruments & coverage mode" subtitle="MONITORING_ONLY instruments are tracked for change but carry no executable applicability rules.">
        <Table head={["Instrument", "Jurisdiction", "Coverage", "Requirements", "Obligation definitions"]} loading={!data && !error}>
          {data?.map((i) => (
            <tr key={i.instrument_id}>
              <td className="td"><div className="font-medium">{i.title}</div><div className="mono text-[11px] text-slate-500">{i.identifier}</div></td>
              <td className="td mono">{i.jurisdiction}</td>
              <td className="td"><CoverageBadge mode={i.coverage_mode} /></td>
              <td className="td">{i.requirements.length}</td>
              <td className="td">{i.obligation_definitions.map((o: any) => <div key={o.obligation_definition_id} className="mono text-xs">{o.key}</div>)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Obligation definitions" subtitle="Binding applicability needs both the definition and its current rule version to be ENCODED.">
        <Table head={["Key", "Title", "Subject type", "Material", "Schedule", "Coverage mode", "Current rule · its coverage"]}>
          {od?.map((o) => (
            <tr key={o.obligation_definition_id}>
              <td className="td mono text-xs">{o.key}</td><td className="td">{o.title}</td>
              <td className="td mono text-xs">{o.subject_type}</td><td className="td">{o.material ? "yes" : "no"}</td>
              <td className="td mono text-xs">{o.schedule_kind}</td>
              <td className="td"><CoverageBadge mode={o.coverage_mode} /></td>
              <td className="td">{o.current_rule_version
                ? <span className="inline-flex flex-wrap items-center gap-1"><span className="mono text-xs">v{o.current_rule_version.version} ·</span> <Hash value={o.current_rule_version.ast_hash} n={8} /> <CoverageBadge mode={o.current_rule_version.coverage_mode} /></span>
                : <span className="text-xs text-slate-500">No published rule version</span>}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Fact definitions">
          <Table head={["Key", "Type", "Attaches to", "Sensitivity", "Manual attestation"]}>
            {fds?.map((f) => (
              <tr key={f.fact_definition_id}>
                <td className="td"><div className="mono text-xs">{f.key}</div><div className="text-[11px] text-slate-500">{f.label}</div></td>
                <td className="td mono text-xs">{f.data_type}</td><td className="td mono text-xs">{f.subject_type}</td>
                <td className="td text-xs">{f.sensitivity}</td><td className="td text-xs">{f.manual_attestation_policy}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Control library & framework mappings" subtitle="One implementation per control/scope; frameworks are mappings, not clones (INV-08).">
          <Table head={["Control", "Category", "Mappings"]}>
            {ctl?.map((c) => (
              <tr key={c.control_definition_id}>
                <td className="td"><div className="mono text-xs">{c.key}</div><div className="text-[11px] text-slate-500">{c.title}</div></td>
                <td className="td text-xs">{c.category}</td>
                <td className="td">{c.framework_mappings.map((m: any) => <div key={m.id} className="text-[11px]"><span className="mono">{m.framework}</span> {m.reference}{m.rights_note && <span className="text-slate-500"> · {m.rights_note}</span>}</div>)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </div>
  );
}
