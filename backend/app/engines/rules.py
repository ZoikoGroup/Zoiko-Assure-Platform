"""Applicability Rule Language (ZA-RUL-001) — pure, typed, side-effect free.

* Canonical typed AST (RuleSchemaVersion 1.0) with stable node ids.
* OperatorSetVersion 1.0 — narrow whitelisted operators, no I/O, no NOW(), no regex,
  no recursion/loops, no implicit coercion.
* Three-valued TRUE/FALSE/UNKNOWN semantics; UNKNOWN never coerces to FALSE.
* Decision tables (ANY_MATCH / SINGLE_MATCH_VALIDATED; FIRST_MATCH prohibited)
  compile to the same AST with row identity preserved in traces.
This module imports nothing that performs I/O (APP-REQ-002 / RUL-INV-05).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

TRUE, FALSE, UNKNOWN, NOT_EVALUATED = "TRUE", "FALSE", "UNKNOWN", "NOT_EVALUATED"
OUTCOME = {TRUE: "APPLIES", FALSE: "DOES_NOT_APPLY", UNKNOWN: "UNCERTAIN"}

SCHEMA_VERSION = "1.0"
OPERATOR_SET_VERSION = "1.0"
# The RuleSchemaVersions and OperatorSetVersions this evaluator implements. RUL-REQ-028: a RuleVersion declaring any
# other version "MUST be rejected explicitly and MUST NOT be interpreted approximately by older runtimes". A version is
# added here only together with the evaluator support for it (and its golden-vector regression, RUL-REQ-017).
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
SUPPORTED_OPERATOR_SET_VERSIONS = frozenset({OPERATOR_SET_VERSION})


def unsupported_versions(rule_schema_version, operator_set_version) -> dict | None:
    """The declared versions this runtime does not implement, or None when both are supported."""
    bad = {k: v for k, v, ok in (("rule_schema_version", rule_schema_version, SUPPORTED_SCHEMA_VERSIONS),
                                 ("operator_set_version", operator_set_version, SUPPORTED_OPERATOR_SET_VERSIONS))
           if v not in ok}
    return bad or None
LIMITS = {"max_depth": 32, "max_nodes": 500, "max_inline_set": 100, "max_table_rows": 250,
          "max_conditions_per_row": 40, "max_source_mappings_per_node": 20}

LOGICAL = {"ALL", "ANY", "NOT"}
VALUE_NODES = {"FACT_REF", "CONST", "SET_REF", "CONTEXT_REF"}
NODE_TYPES = LOGICAL | VALUE_NODES | {"PREDICATE"}
SCALARS = {"BOOLEAN", "STRING", "INTEGER", "DECIMAL", "DATE", "TIMESTAMP", "COUNTRY", "JURISDICTION", "ENUM"}
ORDERED = {"INTEGER", "DECIMAL", "DATE", "TIMESTAMP"}
SIGNATURES = {
    "EQ": 2, "NEQ": 2, "LT": 2, "LTE": 2, "GT": 2, "GTE": 2, "BETWEEN_INCL": 3,
    "IN": 2, "NOT_IN": 2, "INTERSECTS": 2, "CONTAINS_ALL": 2, "EXISTS": 1, "IS_UNKNOWN": 1,
    "DATE_ON_OR_AFTER": 2, "DATE_BEFORE": 2, "SET_SIZE_GTE": 2,
}
PROHIBITED_KEYS = {"script", "code", "regex", "http", "sql", "function", "lambda", "now", "model", "prompt"}


class RuleError(Exception):
    def __init__(self, code: str, message: str, node_id: str | None = None):
        super().__init__(message)
        self.code, self.message, self.node_id = code, message, node_id


class RuleResourceLimitError(RuleError):
    """RUL-REQ-029: the rule exceeds a ZA-RUL-001 §11 complexity limit at runtime. "Budget breach is infrastructure
    error, never UNCERTAIN": the evaluator does not start, and no outcome of any kind is produced."""

    def __init__(self, violations: list[dict]):
        super().__init__("RUL_RESOURCE_LIMIT", " ".join(v["message"] for v in violations))
        self.violations = violations


_LIMIT_ORDER = ("max_depth", "max_nodes", "max_inline_set", "max_table_rows", "max_conditions_per_row",
                "max_source_mappings_per_node")


def _executed_children(n: dict) -> list:
    """The operands the evaluator (_Eval) executes for a node, in its order. A malformed ``children``/``args`` value
    (not a list) has no operands to measure here; validate() reports it as RUL_SCHEMA_INVALID."""
    nt = n.get("node_type")
    if nt in ("ALL", "ANY"):
        ch = n.get("children")
        return list(ch) if isinstance(ch, list) else []
    if nt == "NOT":
        return [n.get("child")]
    if nt == "PREDICATE":
        args = n.get("args")
        return list(args) if isinstance(args, list) else []
    return []


def _table_limit_issues(n: dict, nid) -> list[dict]:
    """ZA-RUL-001 §11 "Maximum decision-table rows 250 / Maximum conditions per row 40: Validation error", measured
    on any node of the AST exactly as resource_limit_violations measures it at runtime (the executed operands of a
    ``decision_table`` root and of a ``row_id`` node), whether the AST was compiled from a table or authored directly,
    and wherever the table or row is nested (RUL-REQ-029)."""
    kids = _executed_children(n)
    out = []
    if n.get("decision_table") and len(kids) > LIMITS["max_table_rows"]:
        out.append(_limit_issue("max_table_rows", f"Decision table {nid} has {len(kids)} rows; the limit is "
                                f"{LIMITS['max_table_rows']}.", nid))
    if "row_id" in n and len(kids) > LIMITS["max_conditions_per_row"]:
        out.append(_limit_issue("max_conditions_per_row", f"Row {n['row_id']} has {len(kids)} conditions; the limit "
                                f"is {LIMITS['max_conditions_per_row']}.", nid))
    return out


def resource_limit_violations(ast: Any) -> list[dict]:
    """The ZA-RUL-001 §11 compiler resource limits (LIMITS), measured on the AST the evaluator is about to execute.
    RUL-REQ-029: "Rule complexity limits MUST be enforced at validation and runtime"; this is the runtime half, run
    before execution starts. Each limit is measured exactly as validation measures it, so a rule that validation accepts
    under the same LIMITS is never refused here:

      max_depth                     the root is depth 1; ALL/ANY children, the NOT child and PREDICATE args are one
                                    level deeper (validate() walk depth);
      max_nodes                     every node object the evaluator can reach (validate() node_count);
      max_inline_set                elements of an inline SET<T> literal (CONST);
      max_source_mappings_per_node  source_mappings on one node;
      max_table_rows                rows of a compiled decision table (children of its decision_table root);
      max_conditions_per_row        conditions of one compiled row (children of a row_id node).

    Every violation is reported, not only the first: per limit in LIMITS order, then in walk order. The walk is
    iterative, so an AST nested beyond the limit cannot exhaust the interpreter stack while it is measured."""
    if not isinstance(ast, dict):
        return []
    found: dict[str, list[dict]] = {k: [] for k in _LIMIT_ORDER}
    nodes, deepest, deepest_id = 0, 0, None
    stack = [(ast, 1)]
    while stack:
        n, depth = stack.pop()
        if not isinstance(n, dict):
            continue
        nodes += 1
        nid = n.get("node_id")
        if depth > deepest:
            deepest, deepest_id = depth, nid
        sm = n.get("source_mappings")
        sm = sm if isinstance(sm, list) else []  # a malformed value maps nothing (validate: RUL_SCHEMA_INVALID)
        if len(sm) > LIMITS["max_source_mappings_per_node"]:
            found["max_source_mappings_per_node"].append(
                {"limit": "max_source_mappings_per_node", "allowed": LIMITS["max_source_mappings_per_node"],
                 "actual": len(sm), "node_id": nid,
                 "message": f"Node {nid} has {len(sm)} source mappings; the limit is {LIMITS['max_source_mappings_per_node']}."})
        if n.get("node_type") == "CONST" and is_set(n.get("type")) and isinstance(n.get("value"), list) \
                and len(n["value"]) > LIMITS["max_inline_set"]:
            found["max_inline_set"].append(
                {"limit": "max_inline_set", "allowed": LIMITS["max_inline_set"], "actual": len(n["value"]), "node_id": nid,
                 "message": f"Inline set {nid} has {len(n['value'])} elements; the limit is {LIMITS['max_inline_set']}."})
        kids = _executed_children(n)
        if n.get("decision_table") and len(kids) > LIMITS["max_table_rows"]:
            found["max_table_rows"].append(
                {"limit": "max_table_rows", "allowed": LIMITS["max_table_rows"], "actual": len(kids), "node_id": nid,
                 "message": f"Decision table {nid} has {len(kids)} rows; the limit is {LIMITS['max_table_rows']}."})
        if "row_id" in n and len(kids) > LIMITS["max_conditions_per_row"]:
            found["max_conditions_per_row"].append(
                {"limit": "max_conditions_per_row", "allowed": LIMITS["max_conditions_per_row"], "actual": len(kids),
                 "node_id": nid,
                 "message": f"Row {n['row_id']} has {len(kids)} conditions; the limit is {LIMITS['max_conditions_per_row']}."})
        stack.extend((c, depth + 1) for c in reversed(kids))
    if deepest > LIMITS["max_depth"]:
        found["max_depth"].append({"limit": "max_depth", "allowed": LIMITS["max_depth"], "actual": deepest,
                                   "node_id": deepest_id,
                                   "message": f"AST depth {deepest} exceeds the limit of {LIMITS['max_depth']}."})
    if nodes > LIMITS["max_nodes"]:
        found["max_nodes"].append({"limit": "max_nodes", "allowed": LIMITS["max_nodes"], "actual": nodes, "node_id": None,
                                   "message": f"The rule has {nodes} nodes; the limit is {LIMITS['max_nodes']}."})
    return [v for k in _LIMIT_ORDER for v in found[k]]


def _issue(code, msg, node_id=None, severity="ERROR"):
    return {"code": code, "severity": severity, "node_id": node_id, "message": msg}


def _limit_issue(limit: str, msg: str, node_id=None) -> dict:
    """A RUL_RESOURCE_LIMIT validation error naming the ZA-RUL-001 §11 limit it enforces (LIMITS key)."""
    return {**_issue("RUL_RESOURCE_LIMIT", msg, node_id), "limit": limit}


# ------------------------------------------------------------------ types
def is_set(t: str | None) -> bool:
    return bool(t) and t.startswith("SET<")


def elem(t: str) -> str:
    return t[4:-1]


def fact_type(fdef: dict) -> str:
    return fdef["data_type"]


def _base(t: str) -> str:
    return "ENUM" if t and t.startswith("ENUM") else t


# ------------------------------------------------------------------ decision tables
def decision_table_shape_problems(table: Any) -> list[dict]:
    """Structural problems that stop a decision table from being compiled at all: the table is an object, ``columns``
    a list of objects each with a string ``column_id`` and ``fact``, ``rows`` a list of objects whose ``cells`` (if
    present) is an object and whose ``row_id`` is a string. Reported as RUL_SCHEMA_INVALID, never raised."""
    if not isinstance(table, dict):
        return [_issue("RUL_SCHEMA_INVALID", "decision_table must be an object.")]
    issues = []
    cols, rows = table.get("columns", []), table.get("rows", [])
    if not isinstance(cols, list) or not all(
            isinstance(c, dict) and isinstance(c.get("column_id"), str) and isinstance(c.get("fact"), str) for c in cols):
        issues.append(_issue("RUL_SCHEMA_INVALID", "columns must be a list of objects with string column_id and fact."))
    if not isinstance(rows, list) or not all(
            isinstance(r, dict) and isinstance(r.get("row_id"), str) and isinstance(r.get("cells", {}), dict)
            for r in rows):
        issues.append(_issue("RUL_SCHEMA_INVALID", "rows must be a list of objects with a string row_id and an object "
                                                   "of cells."))
    return issues


def compile_decision_table(table: dict) -> tuple[dict | None, list[dict]]:
    """Compile a decision table into the canonical AST. Returns (ast, issues); ast is None when the table is too
    malformed to compile (decision_table_shape_problems)."""
    shape = decision_table_shape_problems(table)
    if shape:
        return None, shape
    issues: list[dict] = []
    mode = table.get("table_mode")
    if mode == "FIRST_MATCH":
        issues.append(_issue("RUL_PROHIBITED_CONSTRUCT", "FIRST_MATCH is prohibited for binding rules (RUL-REQ-012)."))
    elif mode not in ("ANY_MATCH", "SINGLE_MATCH_VALIDATED"):
        issues.append(_issue("RUL_SCHEMA_INVALID", f"Unknown table_mode {mode!r}."))
    cols = {c["column_id"]: c for c in table.get("columns", [])}
    rows = table.get("rows", [])
    if len(rows) > LIMITS["max_table_rows"]:
        issues.append(_limit_issue("max_table_rows", "Too many decision-table rows."))
    seen_sigs: dict[str, str] = {}
    row_nodes = []
    for row in rows:
        rid = row.get("row_id")
        preds = []
        cells = row.get("cells", {})
        for cid, col in cols.items():
            if cid not in cells or cells[cid] in (None, ""):
                issues.append(_issue("RUL_DECISION_CELL_BLANK", f"Row {rid} column {cid} is blank; use explicit ANY.", f"row_{rid}"))
                continue
            v = cells[cid]
            if v == "ANY":
                continue
            if isinstance(v, dict) and "set_version_id" in v:
                right = {"node_type": "SET_REF", "node_id": f"row_{rid}_{cid}_set", "set_version_id": v["set_version_id"]}
            else:
                right = {"node_type": "CONST", "node_id": f"row_{rid}_{cid}_val", "type": col.get("value_type", "ENUM"), "value": v}
            preds.append({"node_type": "PREDICATE", "node_id": f"row_{rid}_{cid}", "operator": col.get("operator", "EQ"),
                          "args": [{"node_type": "FACT_REF", "node_id": f"row_{rid}_{cid}_fact", "fact": col["fact"]}, right],
                          "source_mappings": [], "inherits_mapping": True})
        if len(preds) > LIMITS["max_conditions_per_row"]:
            issues.append(_limit_issue("max_conditions_per_row", f"Row {rid} has too many conditions.", f"row_{rid}"))
        if not preds:
            issues.append(_issue("RUL_DECISION_DUPLICATE_ROW", f"Row {rid} is all ANY (unconditional match).", f"row_{rid}"))
        sig = json.dumps(cells, sort_keys=True)
        if sig in seen_sigs:
            issues.append(_issue("RUL_DECISION_DUPLICATE_ROW", f"Row {rid} duplicates row {seen_sigs[sig]}.", f"row_{rid}"))
        seen_sigs[sig] = rid
        if not row.get("source_mappings"):
            issues.append(_issue("RUL_SOURCE_MAPPING_REQUIRED", f"Row {rid} has no source mapping.", f"row_{rid}"))
        node = {"node_type": "ALL", "node_id": f"row_{rid}", "children": preds, "source_mappings": row.get("source_mappings", []),
                "row_id": rid}
        row_nodes.append(node)
    if mode == "SINGLE_MATCH_VALIDATED":
        # static exclusivity analysis: two rows can both match unless some column has distinct EQ constants
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                exclusive = False
                for cid, col in cols.items():
                    a, b = rows[i].get("cells", {}).get(cid), rows[j].get("cells", {}).get(cid)
                    if col.get("operator", "EQ") == "EQ" and a not in (None, "ANY") and b not in (None, "ANY") and a != b:
                        exclusive = True
                if not exclusive:
                    issues.append(_issue("RUL_DECISION_OVERLAP", f"Rows {rows[i]['row_id']} and {rows[j]['row_id']} are not provably exclusive.",
                                         f"row_{rows[i]['row_id']}"))
    root = {"node_type": "ANY", "node_id": table.get("decision_table_id", "dt_root"), "children": row_nodes,
            "source_mappings": table.get("source_mappings", [])}
    if mode == "SINGLE_MATCH_VALIDATED":
        root["single_match"] = True
    root["decision_table"] = True
    return root, issues


# ------------------------------------------------------------------ validation / type checking
def _mapping_refs(n: dict, nid, issues: list) -> list:
    """The source-mapping references a node carries: a list of source_mapping_id strings (ZA-RUL-001 §04 example
    `"source_mappings":["srcmap_42"]`; §08 source_mapping_id). Anything else is a schema error and maps nothing, so a
    malformed value can never make a branch count as mapped."""
    sm = n.get("source_mappings")
    if sm is None or sm == []:
        return []
    if not isinstance(sm, list) or not all(isinstance(r, str) and r.strip() for r in sm):
        issues.append(_issue("RUL_SCHEMA_INVALID", "source_mappings must be a list of source mapping IDs.", nid))
        return []
    return sm


def validate(ast: dict, fact_defs: dict[str, dict], set_versions: dict[str, dict]) -> dict:
    """Schema parse + reference resolution + static type check + lint + limits (ZA-RUL-001 §11)."""
    issues: list[dict] = []
    ids: set[str] = set()
    counter = {"nodes": 0}
    required_facts: set[str] = set()
    set_refs: set[str] = set()

    def walk(n: Any, depth: int, mapped: bool) -> str | None:
        if not isinstance(n, dict):
            issues.append(_issue("RUL_SCHEMA_INVALID", "Node must be an object."))
            return None
        counter["nodes"] += 1
        nid = n.get("node_id")
        if not nid:
            issues.append(_issue("RUL_SCHEMA_INVALID", "Every node requires node_id."))
        elif nid in ids:
            issues.append(_issue("RUL_SCHEMA_INVALID", f"Duplicate node_id {nid}.", nid))
        else:
            ids.add(nid)
        for k in n:
            if k.lower() in PROHIBITED_KEYS:
                issues.append(_issue("RUL_PROHIBITED_CONSTRUCT", f"Prohibited construct '{k}'.", nid))
        if depth > LIMITS["max_depth"]:
            issues.append(_limit_issue("max_depth", "AST depth limit exceeded.", nid))
            return None
        nt = n.get("node_type")
        if nt not in NODE_TYPES:
            issues.append(_issue("RUL_UNKNOWN_NODE", f"Unknown node_type {nt!r}.", nid))
            return None
        issues.extend(_table_limit_issues(n, nid))
        sm = _mapping_refs(n, nid, issues)
        if len(sm) > LIMITS["max_source_mappings_per_node"]:
            issues.append(_limit_issue("max_source_mappings_per_node", "Too many source mappings on node.", nid))
        if "row_id" in n and not sm:
            # §07 source_mapping_refs: "Each row maps to at least one source/interpretation reference", whichever
            # authoring view produced the row (the decision-table compiler reports the same issue for its own rows).
            issues.append(_issue("RUL_SOURCE_MAPPING_REQUIRED", f"Row {n.get('row_id')} has no source mapping.", nid))
        # Mapped means a source mapping actually carried by this node or an enclosing one. inherits_mapping is never
        # proof of anything: it is an ordinary field of the submitted AST, so an author can set it on any node.
        here_mapped = mapped or bool(sm)
        if nt in ("ALL", "ANY"):
            ch = n.get("children") or []
            if not isinstance(ch, list):
                issues.append(_issue("RUL_SCHEMA_INVALID", f"{nt} children must be a list of nodes.", nid))
                return "TRUTH3"
            if len(ch) < (1 if (n.get("row_id") or n.get("decision_table")) else 2):
                issues.append(_issue("RUL_SCHEMA_INVALID", f"{nt} requires >= 2 children.", nid))
            for c in ch:
                t = walk(c, depth + 1, here_mapped)
                if t not in (None, "TRUTH3"):
                    issues.append(_issue("RUL_TYPE_MISMATCH", f"{nt} child must be a truth expression.", nid))
            return "TRUTH3"
        if nt == "NOT":
            issues.append(_issue("RUL_LINT_NOT", "NOT used; prefer positive legal formulation.", nid, "WARNING"))
            t = walk(n.get("child"), depth + 1, here_mapped)
            if t not in (None, "TRUTH3"):
                issues.append(_issue("RUL_TYPE_MISMATCH", "NOT child must be a truth expression.", nid))
            return "TRUTH3"
        if nt == "PREDICATE":
            op = n.get("operator")
            if op not in SIGNATURES:
                issues.append(_issue("RUL_UNKNOWN_OPERATOR", f"Operator {op!r} not in OperatorSetVersion 1.0.", nid))
                return "TRUTH3"
            if not here_mapped:
                # RUL-REQ-008 / §08 branch coverage rule: a source mapping "directly or inherit[ed] ... from an
                # enclosing legal branch with an explicit inheritance flag". The flag marks an inheritance; it cannot
                # create one where no enclosing branch carries a mapping.
                issues.append(_issue("RUL_SOURCE_MAPPING_REQUIRED",
                                     "Predicate sets inherits_mapping but no enclosing legal branch carries a source "
                                     "mapping." if n.get("inherits_mapping") else "Predicate lacks legal source mapping.",
                                     nid))
            args = n.get("args") or []
            if not isinstance(args, list) or len(args) != SIGNATURES[op]:
                issues.append(_issue("RUL_SCHEMA_INVALID", f"{op} expects {SIGNATURES[op]} args.", nid))
                return "TRUTH3"
            types = [walk(a, depth + 1, here_mapped) for a in args]
            if op in ("EXISTS", "IS_UNKNOWN"):
                if not isinstance(args[0], dict) or args[0].get("node_type") != "FACT_REF":
                    issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} accepts FactRef only.", nid))
                if op == "IS_UNKNOWN":
                    issues.append(_issue("RUL_LINT_IS_UNKNOWN", "IS_UNKNOWN is restricted; ensure it is intended.", nid, "WARNING"))
                return "TRUTH3"
            if None in types:
                return "TRUTH3"
            _typecheck(op, types, args, fact_defs, nid, issues)
            if op == "NEQ":
                issues.append(_issue("RUL_LINT_NEQ", "Prefer positive formulation over NEQ.", nid, "WARNING"))
            return "TRUTH3"
        if nt == "FACT_REF":
            key = n.get("fact")
            fd = fact_defs.get(key)
            if not fd:
                issues.append(_issue("RUL_FACT_REF_INVALID", f"Unknown or unreleased FactDefinition {key!r}.", nid))
                return None
            if fd.get("status") not in (None, "RELEASED"):
                issues.append(_issue("RUL_FACT_REF_INVALID", f"FactDefinition {key} is {fd.get('status')}.", nid))
            required_facts.add(key)
            return fact_type(fd)
        if nt == "CONST":
            t = n.get("type")
            v = n.get("value")
            if v is None:
                issues.append(_issue("RUL_SCHEMA_INVALID", "Null literal prohibited.", nid))
                return None
            if is_set(t):
                if not isinstance(v, list):
                    issues.append(_issue("RUL_TYPE_MISMATCH", "Inline set must be a list.", nid))
                elif len(v) > LIMITS["max_inline_set"]:
                    issues.append(_limit_issue("max_inline_set", "Inline set too large; use SetVersion.", nid))
                return t
            if _base(t) not in SCALARS:
                issues.append(_issue("RUL_TYPE_MISMATCH", f"Unknown literal type {t!r}.", nid))
                return None
            if not _literal_ok(t, v):
                issues.append(_issue("RUL_TYPE_MISMATCH", f"Literal {v!r} is not a canonical {t}.", nid))
            return t
        if nt == "SET_REF":
            sv = set_versions.get(n.get("set_version_id"))
            if not sv:
                issues.append(_issue("RUL_SET_REF_INVALID", f"Unknown SetVersion {n.get('set_version_id')!r}.", nid))
                return None
            set_refs.add(n["set_version_id"])
            return f"SET<{sv['element_type']}>"
        if nt == "CONTEXT_REF":
            if n.get("key") != "effective_at":
                issues.append(_issue("RUL_PROHIBITED_CONSTRUCT", "Only CONTEXT_REF(effective_at) is allowed in v1.", nid))
            return "DATE"
        return None

    walk(ast, 1, False)
    if counter["nodes"] > LIMITS["max_nodes"]:
        issues.append(_limit_issue("max_nodes", "Executable node limit exceeded."))
    # RUL-REQ-029 parity: validation must refuse everything the runtime guard (resource_limit_violations) would refuse,
    # so a rule this validator accepts can never fail its budget at evaluation. The walk above stops at the first
    # schema error of a branch; the runtime measures every reachable node, so any limit it finds that the walk did
    # not report is added here, with the runtime's own message and node.
    flagged = {i.get("limit") for i in issues if i["code"] == "RUL_RESOURCE_LIMIT"}
    for v in resource_limit_violations(ast):
        if v["limit"] not in flagged:
            issues.append(_limit_issue(v["limit"], v["message"], v["node_id"]))
    errors = [i for i in issues if i["severity"] == "ERROR"]
    return {"ok": not errors, "issues": issues, "error_count": len(errors),
            "warning_count": len(issues) - len(errors), "required_facts": sorted(required_facts),
            "set_refs": sorted(set_refs), "node_count": counter["nodes"]}


def _literal_ok(t: str, v: Any) -> bool:
    b = _base(t)
    try:
        if b == "BOOLEAN":
            return isinstance(v, bool)
        if b == "INTEGER":
            return isinstance(v, int) and not isinstance(v, bool)
        if b == "DECIMAL":
            return isinstance(v, str) and "e" not in v.lower() and Decimal(v) is not None
        if b == "DATE":
            date.fromisoformat(v)
            return True
        if b == "TIMESTAMP":
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            return True
        return isinstance(v, str)
    except (ValueError, InvalidOperation, TypeError, AttributeError):
        return False


def _compatible(a: str, b: str) -> bool:
    return _base(a) == _base(b)


def _typecheck(op, types, args, fact_defs, nid, issues):
    a = types[0]
    if op in ("EQ", "NEQ"):
        if is_set(a) or is_set(types[1]) or not _compatible(a, types[1]):
            issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} operands {a} vs {types[1]} incompatible.", nid))
        _enum_domain_check(args, fact_defs, nid, issues)
    elif op in ("LT", "LTE", "GT", "GTE", "BETWEEN_INCL", "DATE_ON_OR_AFTER", "DATE_BEFORE"):
        if any(_base(t) not in ORDERED for t in types) or len({_base(t) for t in types}) != 1:
            issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} requires ordered operands of one type; got {types}.", nid))
        if op.startswith("DATE_") and _base(a) != "DATE":
            issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} requires DATE operands.", nid))
    elif op in ("IN", "NOT_IN"):
        if not is_set(types[1]) or is_set(a) or not _compatible(a, elem(types[1])):
            issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} needs T against SET<T>; got {a} / {types[1]}.", nid))
    elif op in ("INTERSECTS", "CONTAINS_ALL"):
        if not (is_set(a) and is_set(types[1]) and _compatible(elem(a), elem(types[1]))):
            issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} needs SET<T> x SET<T>; got {types}.", nid))
    elif op == "SET_SIZE_GTE":
        if not is_set(a) or types[1] != "INTEGER":
            issues.append(_issue("RUL_TYPE_MISMATCH", "SET_SIZE_GTE needs SET<T> x INTEGER.", nid))


def _enum_domain_check(args, fact_defs, nid, issues):
    f = next((x for x in args if x.get("node_type") == "FACT_REF"), None)
    c = next((x for x in args if x.get("node_type") == "CONST"), None)
    if f and c and f.get("fact") in fact_defs:
        dom = fact_defs[f["fact"]].get("enum_domain")
        if dom and c.get("value") not in dom:
            issues.append(_issue("RUL_TYPE_MISMATCH", f"Value {c.get('value')!r} not in domain of {f['fact']}.", nid))


# ------------------------------------------------------------------ canonicalization & hashing
def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonicalize(ast: dict) -> dict:
    """Representation-safe normalization only (no semantic rewrites, RUL-ADR-04)."""
    def norm(n):
        if isinstance(n, dict):
            out = {k: norm(v) for k, v in n.items() if v is not None}
            if out.get("node_type") == "CONST" and _base(out.get("type", "")) == "DECIMAL":
                out["value"] = str(Decimal(out["value"]).normalize())
            return out
        if isinstance(n, list):
            return [norm(x) for x in n]
        return n
    return norm(ast)


def ast_hash(ast: dict, set_hashes: dict[str, str], *, rule_schema_version: str = SCHEMA_VERSION,
             operator_set_version: str = OPERATOR_SET_VERSION) -> str:
    """ZA-RUL-001 §12 hash input. Publication hashes under this build's versions; integrity verification passes the
    versions the RuleVersion declares (RUL-REQ-016: byte-stable under the declared RuleSchemaVersion)."""
    payload = {"rule_schema_version": rule_schema_version, "operator_set_version": operator_set_version,
               "ast": canonicalize(ast), "set_hashes": dict(sorted(set_hashes.items()))}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def set_hash(element_type: str, members: list) -> str:
    return hashlib.sha256(canonical_bytes({"element_type": element_type, "members": sorted(members)})).hexdigest()


# ------------------------------------------------------------------ pure evaluator
def _coerce(t: str, v: Any):
    b = _base(t)
    if is_set(t):
        return frozenset(_coerce(elem(t), x) for x in v)
    if b == "DECIMAL":
        return Decimal(str(v))
    if b == "DATE":
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    if b == "TIMESTAMP":
        return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return v


class _Eval:
    def __init__(self, facts: dict, fact_types: dict, sets: dict, context: dict):
        self.facts, self.fact_types, self.sets, self.context = facts, fact_types, sets, context

    def value(self, n: dict) -> tuple[bool, Any, dict]:
        """Returns (known, value, trace-node)."""
        nt = n["node_type"]
        tr = {"node_id": n["node_id"], "node_type": nt}
        if nt == "FACT_REF":
            key = n["fact"]
            tr["fact"] = key
            f = self.facts.get(key) or {"known": False, "reason": "MISSING_REQUIRED_FACT"}
            if not f.get("known"):
                tr.update(result=UNKNOWN, reason_code=f.get("reason", "MISSING_REQUIRED_FACT"))
                return False, None, tr
            tr.update(result="KNOWN", fact_revision_id=f.get("revision_id"), display_value=f.get("display", f["value"]))
            return True, _coerce(self.fact_types[key], f["value"]), tr
        if nt == "CONST":
            tr["display_value"] = n["value"]
            return True, _coerce(n["type"], n["value"]), tr
        if nt == "SET_REF":
            s = self.sets.get(n["set_version_id"])
            tr["set_version_id"] = n["set_version_id"]
            if s is None:
                tr.update(result=UNKNOWN, reason_code="RULE_GAP")
                return False, None, tr
            return True, frozenset(_coerce(s["element_type"], m) for m in s["members"]), tr
        if nt == "CONTEXT_REF":
            v = self.context["effective_at"]
            tr["display_value"] = str(v)
            return True, _coerce("DATE", v), tr
        raise RuleError("RUL_UNKNOWN_NODE", nt, n.get("node_id"))

    def truth(self, n: dict) -> tuple[str, dict]:
        nt = n["node_type"]
        base = {"node_id": n["node_id"], "node_type": nt, "source_mappings": n.get("source_mappings", [])}
        if "row_id" in n:
            base["row_id"] = n["row_id"]
        if nt in ("ALL", "ANY"):
            children, results = [], []
            decisive = FALSE if nt == "ALL" else TRUE
            single = n.get("single_match")
            stop = False
            for c in n["children"]:
                if stop:
                    children.append(_skipped(c))
                    continue
                r, t = self.truth(c)
                results.append(r)
                children.append(t)
                if r == decisive and not single:
                    stop = True
            if single:
                trues = results.count(TRUE)
                if trues > 1:
                    raise RuleError("RUNTIME_RULE_INVARIANT_BREACH", "SINGLE_MATCH table matched more than one row.", n["node_id"])
                res = TRUE if trues == 1 else (UNKNOWN if UNKNOWN in results else FALSE)
            elif decisive in results:
                res = decisive
            elif UNKNOWN in results:
                res = UNKNOWN
            else:
                res = TRUE if nt == "ALL" else FALSE
            base.update(result=res, children=children)
            return res, base
        if nt == "NOT":
            r, t = self.truth(n["child"])
            res = {TRUE: FALSE, FALSE: TRUE}.get(r, UNKNOWN)
            base.update(result=res, children=[t])
            return res, base
        if nt == "PREDICATE":
            op = n["operator"]
            base["operator"] = op
            if n.get("compiler_generated"):
                base["compiler_generated"] = True
            if op in ("EXISTS", "IS_UNKNOWN"):
                known, _, at = self.value(n["args"][0])
                reason = at.get("reason_code")
                if op == "IS_UNKNOWN":
                    res = FALSE if known else TRUE
                else:
                    res = TRUE if known else (FALSE if reason == "KNOWN_ABSENT" else UNKNOWN)
                base.update(result=res, children=[at])
                if res == UNKNOWN:
                    base["reason_code"] = reason
                return res, base
            vals, traces, unknown_reason = [], [], None
            for a in n["args"]:
                known, v, at = self.value(a)
                traces.append(at)
                if not known:
                    unknown_reason = unknown_reason or at.get("reason_code")
                vals.append((known, v))
            base["children"] = traces
            if not all(k for k, _ in vals):
                base.update(result=UNKNOWN, reason_code=unknown_reason)
                return UNKNOWN, base
            v = [x for _, x in vals]
            res = TRUE if _apply(op, v) else FALSE
            base["result"] = res
            return res, base
        raise RuleError("RUL_UNKNOWN_NODE", str(nt), n.get("node_id"))


def _apply(op: str, v: list) -> bool:
    a = v[0]
    if op == "EQ": return a == v[1]
    if op == "NEQ": return a != v[1]
    if op == "LT": return a < v[1]
    if op == "LTE": return a <= v[1]
    if op == "GT": return a > v[1]
    if op == "GTE": return a >= v[1]
    if op == "BETWEEN_INCL": return v[1] <= a <= v[2]
    if op == "IN": return a in v[1]
    if op == "NOT_IN": return a not in v[1]
    if op == "INTERSECTS": return bool(a & v[1])
    if op == "CONTAINS_ALL": return v[1] <= a
    if op == "DATE_ON_OR_AFTER": return a >= v[1]
    if op == "DATE_BEFORE": return a < v[1]
    if op == "SET_SIZE_GTE": return len(a) >= v[1]
    raise RuleError("RUL_UNKNOWN_OPERATOR", op)


def _skipped(n: dict) -> dict:
    t = {"node_id": n["node_id"], "node_type": n["node_type"], "result": NOT_EVALUATED,
         "short_circuit_reason": "Sibling already determined the parent result."}
    if "row_id" in n:
        t["row_id"] = n["row_id"]
    if n.get("operator"):
        t["operator"] = n["operator"]
    return t


def _collect_unknown_facts(trace: dict, material: bool, mat: set, nonmat: set):
    """Material unknowns lie on UNKNOWN paths whose ancestors are all UNKNOWN."""
    r = trace.get("result")
    if trace.get("node_type") == "FACT_REF" and r == UNKNOWN:
        (mat if material else nonmat).add(trace["fact"])
        return
    child_material = material and r == UNKNOWN
    for c in trace.get("children", []) or []:
        _collect_unknown_facts(c, child_material, mat, nonmat)


def evaluate(ast: dict, facts: dict, fact_types: dict, sets: dict, context: dict) -> dict:
    """Pure deterministic evaluation. ``facts``: key -> {known, value, revision_id} | {known: False, reason}.
    Raises RuleResourceLimitError, before any node executes, when the AST exceeds a complexity limit (RUL-REQ-029)."""
    violations = resource_limit_violations(ast)
    if violations:
        raise RuleResourceLimitError(violations)
    ev = _Eval(facts, fact_types, sets, context)
    res, trace = ev.truth(ast)
    mat, nonmat = set(), set()
    _collect_unknown_facts(trace, res == UNKNOWN, mat, nonmat)
    return {"truth": res, "outcome": OUTCOME[res], "trace": trace,
            "missing_facts": sorted(mat), "non_material_unknowns": sorted(nonmat - mat)}


def run_golden_vectors(ast: dict, vectors: list[dict], fact_defs: dict, sets: dict) -> dict:
    """Execute reviewed golden vectors against exact AST (RUL-REQ-017)."""
    ftypes = {k: v["data_type"] for k, v in fact_defs.items()}
    results = []
    for vec in vectors:
        facts = {}
        for k, val in (vec.get("facts") or {}).items():
            if isinstance(val, dict) and "unknown" in val:
                facts[k] = {"known": False, "reason": val["unknown"]}
            else:
                facts[k] = {"known": True, "value": val, "revision_id": "golden"}
        try:
            out = evaluate(ast, facts, ftypes, sets, {"effective_at": vec.get("effective_at", "2027-12-02")})
            ok = out["outcome"] == vec["expected_outcome"]
            mism = []
            for node_id, exp in (vec.get("trace_assertions") or {}).items():
                got = _find(out["trace"], node_id)
                if not got or got.get("result") != exp:
                    mism.append({"node_id": node_id, "expected": exp, "got": got.get("result") if got else None})
            results.append({"vector_id": vec.get("vector_id"), "description": vec.get("description"),
                            "expected": vec["expected_outcome"], "actual": out["outcome"],
                            "passed": ok and not mism, "trace_mismatches": mism})
        except RuleError as e:
            results.append({"vector_id": vec.get("vector_id"), "passed": False, "error": e.code, "message": e.message})
    passed = sum(1 for r in results if r["passed"])
    classes = {v.get("vector_class") for v in vectors}
    missing = sorted({"POSITIVE", "NEGATIVE", "UNKNOWN"} - classes)
    return {"total": len(results), "passed": passed, "failed": len(results) - passed,
            "all_passed": bool(results) and passed == len(results), "results": results,
            "missing_mandatory_classes": missing, "evaluator_build": "za-applicability-evaluator:1.0.0"}


def _find(trace: dict, node_id: str) -> dict | None:
    if trace.get("node_id") == node_id:
        return trace
    for c in trace.get("children", []) or []:
        f = _find(c, node_id)
        if f:
            return f
    return None
