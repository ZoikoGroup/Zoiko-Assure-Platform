/* Sample records for the Org Workspace prototypes: the fictional Northwind Group (ten-demo-01 in Super Admin Planes,
   seen here from inside its own workspace). Field names, codes and formats follow the application's own records:
   - obligations: lifecycle ADOPTED or CLOSED; owner is the person who adopted it; validity, not "effective", dates;
     the regulatory source (and its effective date) belongs to the requirement, not the obligation;
   - controls: key, title, objective, category; the implementation method is free text; test results are
     PASS · FAIL · ERROR · INCONCLUSIVE · SKIPPED; the current state is a 24-hour preview;
   - evidence: the four classifications, origin and provenance classes, conditions CURRENT · STALE · INVALID · ERASED;
   - events carry no actor. */

export const ORG = { id: "ten-demo-01", name: "Northwind Group", status: "ACTIVE", region: "eu-west (UK/EU)", tier: "POOLED", created: "2026-03-04" };
export const ENTITIES = [
  { id: "nw-gb", name: "Northwind Ltd", country: "GB", ref: "08812345", cell: "eu-west-cell-a", status: "ACTIVE" },
  { id: "nw-nl", name: "Northwind B.V.", country: "NL", ref: "77123456", cell: "eu-west-cell-a", status: "ACTIVE" },
];
export const CELLS = [{ id: "eu-west-cell-a", region: "eu-west (UK/EU)", status: "ACTIVE", tier: "POOLED" }, { id: "uk-south-cell-b", region: "uk-south", status: "STANDBY", tier: "POOLED" }];
export const PERIOD: [string, string] = ["2026-07-01", "2026-10-01"];
export const entityName = (id: string) => ENTITIES.find((e) => e.id === id)?.name ?? id;

export type Source = { title: string; authority: string; jurisdiction: string; effective: string; locator: string };
export type Obligation = {
  lineage: string; key: string; title: string; schedule: "CONTINUOUS" | "RECURRING" | "EVENT_TRIGGERED"; entity: string;
  lifecycle: "ADOPTED" | "CLOSED"; validFrom: string; validTo?: string; owner: string;
  state: "PROVEN" | "NOT_PROVEN" | "FAILING" | "REQUIRES_REVIEW" | null; period?: [string, string]; perspective?: string; cutoff?: number; frozen?: string;
  exceptions: number; source: Source; controls: string[];
};
const DORA: Omit<Source, "locator"> = { title: "Digital Operational Resilience Act, Regulation (EU) 2022/2554", authority: "EU legislature", jurisdiction: "EU", effective: "2025-01-17" };
const NIS2: Omit<Source, "locator"> = { title: "NIS2 Directive (EU) 2022/2555", authority: "EU legislature", jurisdiction: "EU", effective: "2024-10-18" };
const UKGDPR: Omit<Source, "locator"> = { title: "UK GDPR", authority: "UK Parliament", jurisdiction: "GB", effective: "2021-01-01" };
const SYSC: Omit<Source, "locator"> = { title: "FCA Handbook, SYSC", authority: "Financial Conduct Authority", jurisdiction: "GB", effective: "2025-03-31" };

export const OBLIGATIONS: Obligation[] = [
  { lineage: "obl-01", key: "DORA.28.3.REGISTER", title: "Maintain a register of ICT third-party arrangements", schedule: "CONTINUOUS", entity: "nw-nl", lifecycle: "ADOPTED", validFrom: "2026-01-17", owner: "Avery Clarke",
    state: "PROVEN", period: PERIOD, perspective: "RECONSTRUCTED_NOW", cutoff: 4120, frozen: "2026-10-02T08:00:00Z", exceptions: 0, source: { ...DORA, locator: "Art. 28(3)" }, controls: ["C_ICT_THIRD_PARTY_REGISTER"] },
  { lineage: "obl-02", key: "NIS2.23.EARLY_WARNING", title: "Send an early warning of a significant incident within 24 hours", schedule: "EVENT_TRIGGERED", entity: "nw-nl", lifecycle: "ADOPTED", validFrom: "2026-02-01", owner: "Avery Clarke",
    state: "NOT_PROVEN", period: PERIOD, perspective: "RECONSTRUCTED_NOW", cutoff: 4120, frozen: "2026-10-02T08:00:00Z", exceptions: 1, source: { ...NIS2, locator: "Art. 23(4)(a)" }, controls: ["C_INCIDENT_CLASSIFICATION"] },
  { lineage: "obl-03", key: "DORA.19.MAJOR_INCIDENT", title: "Classify and report major ICT-related incidents", schedule: "EVENT_TRIGGERED", entity: "nw-nl", lifecycle: "ADOPTED", validFrom: "2026-01-17", owner: "Sam Rivera",
    state: "FAILING", period: PERIOD, perspective: "RECONSTRUCTED_NOW", cutoff: 4120, frozen: "2026-10-02T08:00:00Z", exceptions: 0, source: { ...DORA, locator: "Art. 19(1)" }, controls: ["C_INCIDENT_CLASSIFICATION"] },
  { lineage: "obl-04", key: "UKGDPR.30.ROPA", title: "Maintain records of processing activities", schedule: "CONTINUOUS", entity: "nw-gb", lifecycle: "ADOPTED", validFrom: "2026-03-10", owner: "Jordan Lee",
    state: "PROVEN", period: PERIOD, perspective: "AS_KNOWN_THEN", cutoff: 4096, frozen: "2026-10-01T09:30:00Z", exceptions: 0, source: { ...UKGDPR, locator: "Art. 30(1)" }, controls: ["C_RECORDS_OF_PROCESSING"] },
  { lineage: "obl-05", key: "SYSC.8.1.OUTSOURCING", title: "Oversee material outsourcing arrangements", schedule: "RECURRING", entity: "nw-gb", lifecycle: "ADOPTED", validFrom: "2026-03-10", owner: "Jordan Lee",
    state: "REQUIRES_REVIEW", period: ["2026-04-01", "2026-07-01"], perspective: "RECONSTRUCTED_NOW", cutoff: 3980, frozen: "2026-07-02T10:00:00Z", exceptions: 0, source: { ...SYSC, locator: "SYSC 8.1.8R" }, controls: ["C_OUTSOURCING_OVERSIGHT", "C_ACCESS_REVIEW"] },
  { lineage: "obl-06", key: "SYSC.27.CERTIFICATION", title: "Certify the fitness and propriety of certified staff each year", schedule: "RECURRING", entity: "nw-gb", lifecycle: "ADOPTED", validFrom: "2026-09-01", owner: "Avery Clarke",
    state: null, exceptions: 0, source: { ...SYSC, locator: "SYSC 27.2" }, controls: ["C_CERTIFICATION_PROCESS"] },
  { lineage: "obl-07", key: "PSD2.96.INCIDENT", title: "Report major operational or security incidents (superseded by DORA)", schedule: "EVENT_TRIGGERED", entity: "nw-nl", lifecycle: "CLOSED", validFrom: "2026-01-01", validTo: "2026-01-17", owner: "Sam Rivera",
    state: "PROVEN", period: ["2026-01-01", "2026-01-17"], perspective: "RECONSTRUCTED_NOW", cutoff: 2210, frozen: "2026-01-18T09:00:00Z", exceptions: 0,
    source: { title: "Payment Services Directive (EU) 2015/2366", authority: "EU legislature", jurisdiction: "EU", effective: "2018-01-13", locator: "Art. 96(1)" }, controls: ["C_INCIDENT_CLASSIFICATION"] },
];

export type Run = { id: string; at: string; result: "PASS" | "FAIL" | "ERROR" | "INCONCLUSIVE" | "SKIPPED"; source: string; window: [string, string]; evidence: string[] };
export type Control = {
  key: string; title: string; objective: string; category: string; entity: string; method: string; owner: string; since: string;
  state: "OPERATING" | "FAILING" | "NOT_EVALUATED" | "DEGRADED" | "REQUIRES_REVIEW"; reasons: string[]; runs: Run[];
};
export const CONTROLS: Control[] = [
  { key: "C_ICT_THIRD_PARTY_REGISTER", title: "ICT third-party register maintenance", objective: "Every ICT third-party arrangement is recorded and reviewed.", category: "Third parties", entity: "nw-nl",
    method: "Register reconciliation against procurement records, run by the procurement sync", owner: "Sam Rivera", since: "2026-01-20", state: "OPERATING", reasons: [],
    runs: [{ id: "run-47", at: "2026-11-03T06:00:00Z", result: "PASS", source: "procurement-sync", window: ["2026-10-01", "2026-11-03"], evidence: ["evd-1042"] },
      { id: "run-41", at: "2026-10-01T07:00:00Z", result: "PASS", source: "procurement-sync", window: ["2026-07-01", "2026-10-01"], evidence: ["evd-1042"] },
      { id: "run-33", at: "2026-07-01T07:00:00Z", result: "PASS", source: "procurement-sync", window: ["2026-06-01", "2026-07-01"], evidence: ["evd-0911"] }] },
  { key: "C_INCIDENT_CLASSIFICATION", title: "Incident classification and reporting", objective: "Incidents are classified against the reporting thresholds and reported on time.", category: "Operations", entity: "nw-nl",
    method: "Incident runbook with classification checklist and timed escalation", owner: "Sam Rivera", since: "2026-01-20", state: "FAILING", reasons: ["TEST_FAIL"],
    runs: [{ id: "run-44", at: "2026-11-02T11:20:00Z", result: "FAIL", source: "native-test", window: ["2026-10-03", "2026-11-03"], evidence: ["evd-1101"] },
      { id: "run-38", at: "2026-09-15T09:00:00Z", result: "PASS", source: "native-test", window: ["2026-08-15", "2026-09-15"], evidence: ["evd-0990"] }] },
  { key: "C_RECORDS_OF_PROCESSING", title: "Records of processing upkeep", objective: "The record of processing is complete and current.", category: "Privacy", entity: "nw-gb",
    method: "Monthly attestation by data owners in the privacy register", owner: "Jordan Lee", since: "2026-03-12", state: "OPERATING", reasons: [],
    runs: [{ id: "run-46", at: "2026-11-02T08:00:00Z", result: "PASS", source: "privacy-register", window: ["2026-10-01", "2026-11-03"], evidence: ["evd-1060"] },
      { id: "run-43", at: "2026-10-05T08:00:00Z", result: "PASS", source: "privacy-register", window: ["2026-09-01", "2026-10-01"], evidence: ["evd-1060"] },
      { id: "run-37", at: "2026-09-02T08:00:00Z", result: "PASS", source: "privacy-register", window: ["2026-08-01", "2026-09-01"], evidence: [] }] },
  { key: "C_OUTSOURCING_OVERSIGHT", title: "Outsourcing oversight review", objective: "Material outsourcing arrangements are overseen and reviewed.", category: "Third parties", entity: "nw-gb",
    method: "Semi-annual oversight committee review", owner: "Jordan Lee", since: "2026-03-12", state: "REQUIRES_REVIEW", reasons: ["SOURCE_CONFLICT"],
    runs: [{ id: "run-40", at: "2026-09-30T15:00:00Z", result: "PASS", source: "native-test", window: ["2026-04-01", "2026-11-03"], evidence: ["evd-1031", "evd-1032"] }] },
  { key: "C_ACCESS_REVIEW", title: "Privileged access review", objective: "Privileged access is reviewed and unneeded access removed.", category: "Access", entity: "nw-gb",
    method: "Monthly privileged access review via identity-provider workflow", owner: "Morgan Hale", since: "2026-03-12", state: "NOT_EVALUATED", reasons: ["TEST_NOT_RUN"],
    runs: [{ id: "run-36", at: "2026-08-31T10:00:00Z", result: "PASS", source: "identity-provider", window: ["2026-08-01", "2026-09-01"], evidence: ["evd-0960"] },
      { id: "run-42", at: "2026-10-01T10:00:00Z", result: "ERROR", source: "identity-provider", window: ["2026-09-01", "2026-10-01"], evidence: [] }] },
  { key: "C_CERTIFICATION_PROCESS", title: "Annual certification process", objective: "Certified staff are assessed and certified each year.", category: "People", entity: "nw-gb",
    method: "Annual HR certification campaign", owner: "Avery Clarke", since: "2026-09-02", state: "NOT_EVALUATED", reasons: ["TEST_NOT_RUN"], runs: [] },
];

export type EvidenceItem = {
  id: string; title: string; classification: "CUSTOMER_INTERNAL" | "CUSTOMER_CONFIDENTIAL" | "RESTRICTED_PERSONAL" | "LEGAL_PRIVILEGED"; entity: string;
  coverage: [string, string]; validUntil: string; condition: "CURRENT" | "STALE" | "INVALID" | "ERASED"; lifecycle: "ACTIVE" | "SUPERSEDED" | "INVALIDATED" | "ERASED";
  origin: string; provenance: string; source: string; ipe: string | null; ledger: number; integrity?: "VERIFIED" | "FAILED" | "PAYLOAD_NOT_AVAILABLE";
  obligations: string[]; controls: string[]; preview: string; hold?: boolean;
};
export const EVIDENCE: EvidenceItem[] = [
  { id: "evd-1042", title: "Q3 ICT third-party register reconciliation", classification: "CUSTOMER_CONFIDENTIAL", entity: "nw-nl", coverage: PERIOD, validUntil: "2026-12-30", condition: "CURRENT", lifecycle: "ACTIVE",
    origin: "SYSTEM_DIRECT", provenance: "P1_SYSTEM_DIRECT", source: "native-test", ipe: "IPE_VERIFIED", ledger: 4088, integrity: "VERIFIED", obligations: ["DORA.28.3.REGISTER"], controls: ["C_ICT_THIRD_PARTY_REGISTER"],
    preview: "Register entries reconciled: 214 of 214\nNew arrangements this quarter: 6\nArrangements without contract reference: 0" },
  { id: "evd-1101", title: "Incident INC-2210 classification record", classification: "CUSTOMER_CONFIDENTIAL", entity: "nw-nl", coverage: ["2026-10-03", "2026-11-02"], validUntil: "2027-01-31", condition: "CURRENT", lifecycle: "ACTIVE",
    origin: "HUMAN_UPLOAD", provenance: "P4_MANUAL_UNATTESTED", source: "upload:INC-2210-classification.pdf", ipe: null, ledger: 4151, integrity: "VERIFIED", obligations: ["DORA.19.MAJOR_INCIDENT", "NIS2.23.EARLY_WARNING"], controls: ["C_INCIDENT_CLASSIFICATION"],
    preview: "PDF document (3 pages). Payloads are decrypted only for people allowed to read this classification." },
  { id: "evd-1060", title: "September records-of-processing attestation", classification: "RESTRICTED_PERSONAL", entity: "nw-gb", coverage: ["2026-09-01", "2026-10-01"], validUntil: "2026-12-30", condition: "CURRENT", lifecycle: "ACTIVE",
    origin: "SYSTEM_DIRECT", provenance: "P1_SYSTEM_DIRECT", source: "privacy-register", ipe: "IPE_VERIFIED", ledger: 4110, integrity: "VERIFIED", obligations: ["UKGDPR.30.ROPA"], controls: ["C_RECORDS_OF_PROCESSING"],
    preview: "Contains personal data: preview withheld. Open the record to request the payload." },
  { id: "evd-0960", title: "August privileged access review export", classification: "CUSTOMER_CONFIDENTIAL", entity: "nw-gb", coverage: ["2026-08-01", "2026-09-01"], validUntil: "2026-10-01", condition: "STALE", lifecycle: "ACTIVE",
    origin: "SYSTEM_DIRECT", provenance: "P1_SYSTEM_DIRECT", source: "identity-provider", ipe: "IPE_INCONCLUSIVE", ledger: 3990, integrity: "VERIFIED", obligations: ["SYSC.8.1.OUTSOURCING"], controls: ["C_ACCESS_REVIEW"],
    preview: "Privileged accounts reviewed: 38\nAccess removed: 3\nReviewer sign-off: complete" },
  { id: "evd-1031", title: "Outsourcing committee minutes (draft)", classification: "LEGAL_PRIVILEGED", entity: "nw-gb", coverage: ["2026-04-01", "2026-10-01"], validUntil: "2026-12-30", condition: "INVALID", lifecycle: "INVALIDATED",
    origin: "HUMAN_UPLOAD", provenance: "P4_MANUAL_UNATTESTED", source: "upload:committee-minutes-draft.docx", ipe: null, ledger: 4070, integrity: "VERIFIED", obligations: ["SYSC.8.1.OUTSOURCING"], controls: ["C_OUTSOURCING_OVERSIGHT"],
    preview: "Invalidated: superseded by the approved minutes. Legal privilege: preview withheld.", hold: true },
];

export type Activity = { seq: number; at: string; event: string; aggregate: string; id: string };
export const ACTIVITY: Activity[] = [
  { seq: 4151, at: "2026-11-02T11:22:00Z", event: "evidence.captured", aggregate: "EvidenceArtifact", id: "evd-1101" },
  { seq: 4150, at: "2026-11-02T11:21:00Z", event: "control.evaluated", aggregate: "ControlImplementation", id: "C_INCIDENT_CLASSIFICATION" },
  { seq: 4149, at: "2026-11-02T11:20:00Z", event: "test.run_recorded", aggregate: "TestRun", id: "run-44" },
  { seq: 4140, at: "2026-10-27T16:05:00Z", event: "exception.approved", aggregate: "Exception", id: "exc-31" },
  { seq: 4132, at: "2026-10-26T09:12:00Z", event: "determination.challenged", aggregate: "ApplicabilityDetermination", id: "det-22" },
  { seq: 4120, at: "2026-10-02T08:00:00Z", event: "assurance.snapshot_created", aggregate: "AssuranceSnapshot", id: "snap-301" },
];

/* Exceptions record target, reason (required), validity, requester, approver and state. They carry no request date,
   requester role or evidence link today, so those are proposed fields (`proposed`). `byYou` marks the sample request
   treated as yours, to show separation of duties. */
export type ExceptionReq = {
  id: string; targetType: "OBLIGATION" | "CONTROL"; target: string; reason: string; validFrom: string; validTo: string;
  requestedBy: string; approvedBy?: string; state: "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "REVOKED"; seq: number; byYou?: boolean;
  proposed: { requested: string; role: string; evidence: string[] };
};
export const EXCEPTIONS: ExceptionReq[] = [
  { id: "exc-34", targetType: "OBLIGATION", target: "NIS2.23.EARLY_WARNING", validFrom: "2026-11-01", validTo: "2026-12-01", requestedBy: "Jordan Lee", state: "PENDING_APPROVAL", seq: 4160,
    reason: "The CSIRT reporting portal account is being re-provisioned; early warnings go by the CSIRT's secure email route until 30 November.",
    proposed: { requested: "2026-10-30", role: "Control Owner", evidence: ["evd-1101"] } },
  { id: "exc-35", targetType: "CONTROL", target: "C_ACCESS_REVIEW", validFrom: "2026-10-01", validTo: "2026-11-15", requestedBy: "Morgan Hale", state: "PENDING_APPROVAL", seq: 4162,
    reason: "The identity-provider export failed in September; the review is being re-run from the access log while the connector is repaired.",
    proposed: { requested: "2026-10-31", role: "Control Owner", evidence: ["evd-0960"] } },
  { id: "exc-36", targetType: "CONTROL", target: "C_CERTIFICATION_PROCESS", validFrom: "2026-11-01", validTo: "2027-01-31", requestedBy: "You", state: "PENDING_APPROVAL", seq: 4166, byYou: true,
    reason: "The annual certification campaign moves to January so it follows the new HR system cut-over.",
    proposed: { requested: "2026-11-02", role: "Your role", evidence: [] } },
  { id: "exc-31", targetType: "OBLIGATION", target: "NIS2.23.EARLY_WARNING", validFrom: "2026-10-15", validTo: "2026-12-15", requestedBy: "Sam Rivera", approvedBy: "Avery Clarke", state: "APPROVED", seq: 4140,
    reason: "The out-of-hours escalation rota is short-staffed during the on-call tooling migration; manual paging covers the gap.",
    proposed: { requested: "2026-10-24", role: "Control Owner", evidence: [] } },
  { id: "exc-27", targetType: "CONTROL", target: "C_OUTSOURCING_OVERSIGHT", validFrom: "2026-09-01", validTo: "2026-12-31", requestedBy: "Jordan Lee", state: "REJECTED", seq: 4012,
    reason: "Committee review deferred to the year-end meeting.",
    proposed: { requested: "2026-08-28", role: "Control Owner", evidence: ["evd-1031"] } },
];

/* Compensations record no proposer and no request date; approving one creates a new satisfaction-expression version. */
export type CompensationReq = {
  id: string; obligation: string; primary: string; compensating: string[]; rationale: string; validFrom: string; validTo?: string;
  status: "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "REVOKED"; approvedBy?: string; seq: number;
};
export const COMPENSATIONS: CompensationReq[] = [
  { id: "cmp-07", obligation: "SYSC.8.1.OUTSOURCING", primary: "C_ACCESS_REVIEW", compensating: ["C_OUTSOURCING_OVERSIGHT"], validFrom: "2026-10-01", status: "PENDING_APPROVAL", seq: 4164,
    rationale: "The oversight committee's quarterly vendor access attestation covers the same privileged accounts while the automated review is unavailable." },
  { id: "cmp-04", obligation: "DORA.19.MAJOR_INCIDENT", primary: "C_INCIDENT_CLASSIFICATION", compensating: ["C_ICT_THIRD_PARTY_REGISTER"], validFrom: "2026-06-01", validTo: "2026-09-01", status: "REJECTED", seq: 3840,
    rationale: "Register reconciliation would identify affected providers; rejected because it does not classify incidents." },
];

/* Applicability determinations and their attestations (stored as ATTESTED or DECLINED, each with an authority basis). */
export type Attestation = { at: string; actor: string; decision: "ATTESTED" | "DECLINED"; authority: string; reason?: string; seq: number };
export type Determination = {
  lineage: string; obligation: string; title: string; entity: string; outcome: "APPLIES" | "DOES_NOT_APPLY" | "UNCERTAIN";
  state: "PROPOSED" | "IN_REVIEW" | "ADOPTED" | "REJECTED"; needsReview?: string; basis: string; validFrom: string; evaluated: string; rule: string;
  facts: [string, string][]; reasons: string[]; evidence: string[]; attestations: Attestation[];
};
export const DETERMINATIONS: Determination[] = [
  { lineage: "det-24", obligation: "SYSC.27.CERTIFICATION", title: "Certify the fitness and propriety of certified staff each year", entity: "nw-gb", outcome: "APPLIES", state: "PROPOSED",
    basis: "Northwind Ltd is an FCA-authorised firm with staff in certification functions.", validFrom: "2026-09-01", evaluated: "2026-10-29T14:10:00Z", rule: "FCA.SYSC.27 · v3",
    facts: [["fca_authorised", "true"], ["certification_function_headcount", "12"]], reasons: [], evidence: [], attestations: [] },
  { lineage: "det-22", obligation: "NIS2.23.EARLY_WARNING", title: "Send an early warning of a significant incident within 24 hours", entity: "nw-nl", outcome: "APPLIES", state: "IN_REVIEW",
    basis: "Northwind B.V. provides managed ICT services in the Netherlands above the size threshold.", validFrom: "2026-02-01", evaluated: "2026-10-20T09:00:00Z", rule: "EU.NIS2.23 · v2",
    facts: [["sector", "ICT service management (B2B)"], ["employee_count", "260"], ["annual_turnover_eur", "61 000 000"]], reasons: [], evidence: ["evd-1101"], attestations: [] },
  { lineage: "det-21", obligation: "EU.AIA.26.DEPLOYER", title: "Meet deployer obligations for high-risk AI systems", entity: "nw-gb", outcome: "UNCERTAIN", state: "PROPOSED",
    basis: "Whether the credit-scoring model is a high-risk AI system is not yet established.", validFrom: "2026-08-02", evaluated: "2026-10-12T11:45:00Z", rule: "EU.AIA.26 · v1",
    facts: [["uses_ai_credit_scoring", "true"], ["ai_system_risk_class", "missing"]], reasons: ["MISSING_REQUIRED_FACT"], evidence: [], attestations: [] },
  { lineage: "det-19", obligation: "DORA.28.3.REGISTER", title: "Maintain a register of ICT third-party arrangements", entity: "nw-nl", outcome: "APPLIES", state: "ADOPTED",
    basis: "Northwind B.V. is an electronic money institution within DORA scope.", validFrom: "2026-01-17", evaluated: "2026-01-12T10:00:00Z", rule: "EU.DORA.28 · v1",
    facts: [["entity_type", "Electronic money institution"], ["eu_authorised", "true"]], reasons: [], evidence: ["evd-0911"],
    attestations: [{ at: "2026-01-16T15:30:00Z", actor: "Avery Clarke", decision: "ATTESTED", authority: "Chief Compliance Officer, board delegation of 2025-11-12", seq: 2190 }] },
  { lineage: "det-17", obligation: "UKGDPR.37.DPO", title: "Designate a data protection officer", entity: "nw-gb", outcome: "APPLIES", state: "REJECTED",
    basis: "The evaluation treats the customer-analytics programme as large-scale monitoring.", validFrom: "2026-03-10", evaluated: "2026-03-08T09:20:00Z", rule: "UK.GDPR.37 · v1",
    facts: [["large_scale_monitoring", "true"], ["special_category_core_activity", "false"]], reasons: [], evidence: [],
    attestations: [{ at: "2026-03-09T16:00:00Z", actor: "Jordan Lee", decision: "DECLINED", authority: "Head of Legal, General Counsel delegation of 2026-01-05", reason: "The analytics programme is aggregate-only; legal review found no large-scale monitoring of individuals.", seq: 2604 }] },
];

/* Packages are built from each obligation's latest frozen snapshot and stored SIGNED. */
export type PackageRec = { id: string; entity: string; period: [string, string]; cutoff: number; obligations: string[]; manifest: string; signer: string; created: string; createdBy: string };
export const PACKAGES: PackageRec[] = [
  { id: "pkg-0012", entity: "nw-nl", period: ["2026-04-01", "2026-07-01"], cutoff: 3986, obligations: ["DORA.28.3.REGISTER", "NIS2.23.EARLY_WARNING", "DORA.19.MAJOR_INCIDENT"],
    manifest: "9f2c41d07ab3e8155c6d0e2f4b7a91c3d8e5f6a7b2c1d0e9f8a7b6c5d4e3f2a1", signer: "za-sign-2026-01", created: "2026-07-03T10:12:00Z", createdBy: "Avery Clarke" },
  { id: "pkg-0009", entity: "nw-gb", period: ["2026-04-01", "2026-07-01"], cutoff: 3990, obligations: ["UKGDPR.30.ROPA", "SYSC.8.1.OUTSOURCING"],
    manifest: "4b1e7c93a2d5f6081c3e9b7a5d4f2e1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e", signer: "za-sign-2026-01", created: "2026-07-04T08:40:00Z", createdBy: "Jordan Lee" },
];
