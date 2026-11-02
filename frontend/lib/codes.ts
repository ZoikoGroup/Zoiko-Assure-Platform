/* Controlled code sets that the backend accepts as free text without checking (audit FA-29). /dictionary does not
   publish them, so they are taken from the specifications. Forms offer only these values; a stored value outside its
   set is shown as stored and marked, never rewritten. */

/** ZA-DOM-001 §17.1 data classes, each with the specification's example. */
export const DATA_CLASSES: Record<string, string> = {
  PUBLIC_REGULATORY: "Official law/guidance metadata",
  CUSTOMER_INTERNAL: "Entity facts, mappings, workflow state",
  CUSTOMER_CONFIDENTIAL: "Contracts, internal controls, audit findings",
  RESTRICTED_PERSONAL: "Personal/special-category evidence where unavoidable",
  LEGAL_PRIVILEGED: "Material marked legally privileged by customer policy",
  SECURITY_SECRET: "Tokens, keys, raw credentials",
};
export const DATA_CLASS_CODES = Object.keys(DATA_CLASSES);

/** ZA-RUL-001 §14 rule change classes. */
export const RULE_CHANGE_CLASSES = ["EDITORIAL_METADATA", "SOURCE_MAPPING", "EXECUTABLE_NON_OUTCOME", "EXECUTABLE_OUTCOME", "TEMPORAL", "DEPENDENCY", "CORRECTION"];

/** ZA-ARCH-001 §12.1 AI asset registry kinds, in specification order. */
export const AI_ASSET_KINDS = ["MODEL", "FOUNDATION_MODEL", "AI_APPLICATION", "COPILOT", "AGENT", "MULTI_AGENT_SYSTEM", "TOOL", "MCP_SERVER", "KNOWLEDGE_SOURCE", "EVALUATION_SUITE"];

/** A stored code for display: as stored, marked when it is outside its set, and "Not set" when empty. */
export function codeText(code: string | null | undefined, set: readonly string[]): string {
  if (!code) return "Not set";
  return set.includes(code) ? code : `${code} (non-canonical)`;
}
