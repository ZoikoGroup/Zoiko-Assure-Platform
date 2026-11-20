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
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import codes  # constants only (no I/O): the one evaluator build identifier, read at call time

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
# ZA-DAT-001 typed fact value column value_decimal: numeric(38,10) (migration 0001), i.e. at most 28 integer and 10
# fractional digits. RUL §05: DECIMAL is "Exact decimal arithmetic"; NaN, sNaN and +/-Infinity are not decimal values.
DECIMAL_PRECISION, DECIMAL_SCALE = 38, 10


def decimal_value_ok(d: Decimal) -> bool:
    """A finite decimal the value_decimal column holds exactly: no rounding of the fraction (PostgreSQL rounds beyond
    the scale silently) and no integer part the column cannot hold. Trailing zeros carry no digits. Pure: no context
    arithmetic, so no rounding happens while checking."""
    if not isinstance(d, Decimal) or not d.is_finite():
        return False
    if d.is_zero():
        return True
    _, digits, exp = d.as_tuple()
    digits = list(digits)
    while exp < 0 and digits[-1] == 0:
        digits.pop()
        exp += 1
    return exp >= -DECIMAL_SCALE and len(digits) + exp <= DECIMAL_PRECISION - DECIMAL_SCALE


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
    # A type that is not a string (a malformed `type` field) is no type at all: never a set, and never raised on, so
    # validation reports it as a schema error and the runtime limit guard can still measure the AST.
    return isinstance(t, str) and t.startswith("SET<")


def elem(t: str) -> str:
    return t[4:-1]


def fact_type(fdef: dict) -> str:
    return fdef["data_type"]


def _base(t: str) -> str:
    return "ENUM" if isinstance(t, str) and t.startswith("ENUM") else t


def legal_date(v: Any) -> date:
    """The legal calendar date an effective_at denotes, for rule selection and for rules that compare the effective date
    (CONTEXT_REF effective_at). Owner decision (TIM owner and RUL owner), "Option U", for the explicit conversion rule
    ZA-TIM-001 §02 requires for mixed-profile comparison: an offset-aware instant is converted to UTC and its UTC calendar
    date is used, so "2027-01-01T23:30:00-05:00" and "2027-01-02T04:30:00Z" are both 2027-01-02. The one conversion every
    path uses (golden vectors, sandbox, live evaluation and replay, shadow, publication impact, catalogue selection).
    A date is itself; a date-time without an offset keeps its written date (unchanged: it is not an instant, and live
    evaluation already reads such values as UTC). Nothing else is converted; invalid text is a RUL_TYPE_MISMATCH."""
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        return v
    else:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            raise RuleError("RUL_TYPE_MISMATCH", f"{v!r} is not an ISO DATE value and cannot be compared.") from None
    if d.utcoffset() is not None:
        try:
            d = d.astimezone(timezone.utc)
        except OverflowError:
            # Its UTC conversion leaves the calendar (year 1 east of UTC, year 9999 west of it): it has no UTC date, so
            # it cannot be read either. Refused as unreadable input is, not an unhandled OverflowError (a 500).
            raise RuleError("RUL_TYPE_MISMATCH", f"{v!r} has no UTC calendar date (outside years 1-9999).") from None
    return d.date()


def timestamp_value(v: Any) -> datetime | None:
    """The instant a TIMESTAMP value denotes, or None when it is not one. ZA-RUL-001 §03: TIMESTAMP is an "Offset-aware
    canonical instant" and date/time literals use "ISO 8601 canonical form; no ambiguous local date/time strings". A
    string without a UTC offset (or a date alone) is a local date/time, not an instant: comparing it with an instant is
    undefined (Python raises, or calls them unequal), and reading it as UTC would be the implicit timezone conversion
    §05 prohibits. Used alike by literal validation, golden-vector and fixture inputs, fact writes and the evaluator."""
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return d if d.utcoffset() is not None else None


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
    # Cells are addressed by column_id, so two columns sharing one id cannot both be compiled: the later one replaced
    # the earlier and its condition vanished from the AST (RUL-INV-10: no semantic loss between authoring view and AST).
    column_ids = [c["column_id"] for c in table.get("columns", [])]
    for cid in dict.fromkeys(i for i in column_ids if column_ids.count(i) > 1):
        issues.append(_issue("RUL_SCHEMA_INVALID", f"Column id {cid} is declared more than once; each input column "
                                                   "needs its own column_id."))
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
        # §11 stage 1 "unknown ... field rejected"; §07 conditions are input columns. A cell naming no declared column was
        # never compiled, so the condition a reviewer read in the table was absent from the executable AST.
        for k in cells:
            if k not in cols:
                issues.append(_issue("RUL_SCHEMA_INVALID", f"Row {rid} has a cell for undeclared column {k!r}; every "
                                                           "condition must be a declared input column.", f"row_{rid}"))
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
        # §18 "Semantically duplicate row": cells compared as the evaluator compares them (_cell_key), so "1.0" / "1.00"
        # or one instant written with two offsets are one condition, not two rows that happen to be spelled differently.
        sig = tuple(sorted(((k, _cell_key(cols[k].get("value_type", "ENUM") if k in cols else None, v))
                            for k, v in cells.items()), key=lambda kv: kv[0]))
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
                    if col.get("operator", "EQ") == "EQ" and a not in (None, "ANY") and b not in (None, "ANY") \
                            and _distinct_values(col.get("value_type", "ENUM"), a, b):
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


def _cell_key(value_type: Any, v: Any) -> tuple:
    """What one decision-table cell compares as, for duplicate-row detection. A valid literal of the column type
    (_literal_ok, every member of a SET<T>) is its value as the evaluator compares it (_coerce): DECIMAL by exact value
    (RUL §03 "Exact decimal arithmetic"), DATE by calendar date, TIMESTAMP by instant ("Offset-aware canonical instant"),
    SET<T> by membership; text types as written (no case or Unicode normalization is defined, §03). Anything else (ANY,
    a SetRef, an invalid literal, a cell of no declared column) is compared as written: never merged with a valid value,
    and validation reports the invalid literal itself."""
    if isinstance(value_type, str) and v != "ANY" and not isinstance(v, dict):
        try:
            if is_set(value_type):
                et = elem(value_type) if value_type.endswith(">") else ""
                valid = _base(et) in SCALARS and isinstance(v, list) and all(_literal_ok(et, m) for m in v)
            else:
                valid = _base(value_type) in SCALARS and _literal_ok(value_type, v)
            if valid:
                return ("value", _coerce(value_type, v))
        except (RuleError, ValueError, TypeError):
            pass
    return ("as_written", json.dumps(v, sort_keys=True))


def _as_text(x: Any) -> str:
    return json.dumps(x, sort_keys=True, default=str)


def _condition_key(n: Any) -> tuple:
    """What one node of an AST decision-table row compares as, for duplicate-row detection (team-lead decision TL-3).
    Exact structure, in source order: the same node types, operators, facts, set references and context keys, and
    literals compared as decision-table cells are (_cell_key: a valid literal by the evaluator's typed value, anything
    else as written). No reordering of conditions, no operator equivalence (DATE_ON_OR_AFTER is not GTE), no flattening
    of nested logic; node IDs, source mappings and other annotations are not conditions. A node that cannot be read is
    never equal to anything."""
    if not isinstance(n, dict):
        return ("UNCOMPARABLE", id(n))
    nt = n.get("node_type")
    if nt == "PREDICATE":
        args = n.get("args")
        return ("PREDICATE", _as_text(n.get("operator")),
                tuple(_condition_key(a) for a in args) if isinstance(args, list) else ("UNCOMPARABLE", id(n)))
    if nt == "FACT_REF":
        return ("FACT_REF", _as_text(n.get("fact")))
    if nt == "CONST":
        return ("CONST", _as_text(n.get("type")), _cell_key(n.get("type"), n.get("value")))
    if nt == "SET_REF":
        return ("SET_REF", _as_text(n.get("set_version_id")))
    if nt == "CONTEXT_REF":
        return ("CONTEXT_REF", _as_text(n.get("key")))
    if nt in ("ALL", "ANY"):
        ch = n.get("children")
        return (nt, n.get("single_match") is True, n.get("decision_table") is True,
                tuple(_condition_key(c) for c in ch) if isinstance(ch, list) else ("UNCOMPARABLE", id(n)))
    if nt == "NOT":
        return ("NOT", _condition_key(n.get("child")))
    return ("UNCOMPARABLE", id(n))


def _ast_table_issues(n: dict, nid, nt: str, table_rows: set) -> list[dict]:
    """Decision-table structure written directly as AST (authoring_mode AST, or a compiled table's AST re-authored).

    TL-1 (team-lead decision; the compiler's output shape, compile_decision_table): a decision table is an ANY node with
    `decision_table: true`, and its rows are its children, each an ALL node with a string row_id. TL-2: other uses of that
    metadata are refused as RUL_SCHEMA_INVALID, not ignored: the flag on another node type or with a non-boolean value, a
    table child that is not a row, a row_id outside a table or not a non-empty string, a row_id repeated in one table
    (§07 "Stable row_id"; RUL-REQ-025). Rows of one table that are the same conditions (TL-3, _condition_key) are
    RUL_DECISION_DUPLICATE_ROW, reported exactly as the decision-table compiler reports them, so a compiled table is not
    reported twice. `table_rows` collects the row nodes of tables already visited (the walk visits a table before its
    rows)."""
    out = []
    flag = n.get("decision_table")
    if flag not in (None, False) and (flag is not True or nt != "ANY"):
        out.append(_issue("RUL_SCHEMA_INVALID", f"decision_table must be a boolean and marks only an ANY node (the table "
                                                f"root); found {flag!r} on {nt}.", nid))
    if "row_id" in n:
        rid = n["row_id"]
        if not (isinstance(rid, str) and rid.strip()):
            out.append(_issue("RUL_SCHEMA_INVALID", f"row_id must be a non-empty string, not {rid!r}.", nid))
        elif id(n) not in table_rows:
            out.append(_issue("RUL_SCHEMA_INVALID", f"Row {rid} is not a row of a decision table: a row_id marks an ALL "
                                                    "child of an ANY node with decision_table true.", nid))
    children = n.get("children")
    if flag is not True or nt != "ANY" or not isinstance(children, list):
        return out
    ids: set = set()
    seen: dict = {}
    for c in children:
        if not (isinstance(c, dict) and c.get("node_type") == "ALL" and "row_id" in c):
            out.append(_issue("RUL_SCHEMA_INVALID", f"Decision table {nid}: every child must be a row (an ALL node with a "
                                                    "row_id).", nid))
            continue
        table_rows.add(id(c))
        rid = c["row_id"]
        if not (isinstance(rid, str) and rid.strip()):
            continue  # reported on the row itself
        row_nid = c.get("node_id") if isinstance(c.get("node_id"), str) else None
        if rid in ids:
            out.append(_issue("RUL_SCHEMA_INVALID", f"Decision table {nid} has row_id {rid} more than once.", row_nid))
            continue
        ids.add(rid)
        ch = c.get("children")
        key = tuple(_condition_key(x) for x in ch) if isinstance(ch, list) else ("UNCOMPARABLE", id(c))
        if key in seen:
            out.append(_issue("RUL_DECISION_DUPLICATE_ROW", f"Row {rid} duplicates row {seen[key]}.", row_nid))
        else:
            seen[key] = rid
    return out


def _distinct_values(value_type: Any, a: Any, b: Any) -> bool:
    """Whether two EQ cell constants of one column can never both equal a fact value (RUL-REQ-013 exclusivity proof).
    They are compared as the evaluator compares them (_coerce): "1.0" and "1.00" are one DECIMAL, and two spellings of
    one instant are one TIMESTAMP, so different text is not proof that two rows cannot both match ("More than one TRUE:
    RUNTIME_RULE_INVARIANT_BREACH ... Publication should have prevented it", RUL §07). A cell that is not a valid
    literal of the column type proves nothing (validation reports the literal itself)."""
    try:
        return _coerce(value_type, a) != _coerce(value_type, b)
    except (RuleError, ValueError, TypeError):
        return False


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
    table_rows: set[int] = set()  # row nodes of decision tables already visited (_ast_table_issues)

    def walk(n: Any, depth: int, mapped: bool) -> str | None:
        if not isinstance(n, dict):
            issues.append(_issue("RUL_SCHEMA_INVALID", "Node must be an object."))
            return None
        counter["nodes"] += 1
        nid = n.get("node_id")
        if not nid:
            issues.append(_issue("RUL_SCHEMA_INVALID", "Every node requires node_id."))
        elif not isinstance(nid, str):  # §11 stage 1: a schema error, reported, never raised (it is not hashable)
            issues.append(_issue("RUL_SCHEMA_INVALID", f"node_id must be a string, not {nid!r}."))
            nid = None
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
        if not isinstance(nt, str) or nt not in NODE_TYPES:
            issues.append(_issue("RUL_UNKNOWN_NODE", f"Unknown node_type {nt!r}.", nid))
            return None
        # single_match is the compiled form of a SINGLE_MATCH_VALIDATED table (RUL §07): a boolean on the ANY node over
        # the rows. No other node has single-match semantics; on ALL the evaluator applied them anyway and contradicted
        # the §06 ALL truth table (ALL(TRUE, FALSE) evaluated TRUE), and a non-boolean value ("false") read as true.
        if n.get("single_match") not in (None, False) and (n["single_match"] is not True or nt != "ANY"):
            issues.append(_issue("RUL_SCHEMA_INVALID", f"single_match must be a boolean and is defined only on an ANY "
                                                       f"node (SINGLE_MATCH_VALIDATED rows); found {n['single_match']!r} "
                                                       f"on {nt}.", nid))
        issues.extend(_table_limit_issues(n, nid))
        issues.extend(_ast_table_issues(n, nid, nt, table_rows))
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
            if not isinstance(op, str) or op not in SIGNATURES:
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
            fd = fact_defs.get(key) if isinstance(key, str) else None
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
            if not isinstance(t, str):
                issues.append(_issue("RUL_SCHEMA_INVALID", f"Literal type must be a type name, not {t!r}.", nid))
                return None
            if v is None:
                issues.append(_issue("RUL_SCHEMA_INVALID", "Null literal prohibited.", nid))
                return None
            if is_set(t):
                # RUL §04 "SET<T>: Elements same type T": the element type is a scalar literal type, and every member
                # is a literal of it, checked exactly as a scalar literal (_literal_ok: no coercion, canonical decimal
                # strings, ISO dates); a null member is a null literal ("Null: Not permitted as a literal"). The
                # specification has no rule for an empty set or a repeated member: both keep their set meaning.
                et = elem(t) if t.endswith(">") else ""
                if _base(et) not in SCALARS:
                    issues.append(_issue("RUL_TYPE_MISMATCH", f"Unknown literal type {t!r}.", nid))
                if not isinstance(v, list):
                    issues.append(_issue("RUL_TYPE_MISMATCH", "Inline set must be a list.", nid))
                elif len(v) > LIMITS["max_inline_set"]:
                    issues.append(_limit_issue("max_inline_set", "Inline set too large; use SetVersion.", nid))
                elif _base(et) in SCALARS:
                    for i, m in enumerate(v):
                        if m is None:
                            issues.append(_issue("RUL_SCHEMA_INVALID", f"Inline set member {i} is null; null literal "
                                                 "prohibited.", nid))
                        elif not _literal_ok(et, m):
                            issues.append(_issue("RUL_TYPE_MISMATCH", f"Inline set member {i} {m!r} is not a "
                                                 f"canonical {et}.", nid))
                return t
            if _base(t) not in SCALARS:
                issues.append(_issue("RUL_TYPE_MISMATCH", f"Unknown literal type {t!r}.", nid))
                return None
            if not _literal_ok(t, v):
                issues.append(_issue("RUL_TYPE_MISMATCH", f"Literal {v!r} is not a canonical {t}.", nid))
            elif _base(t) == "DECIMAL":
                canonical = decimal_canonicalization_loss(t, v)
                if canonical is not None:
                    issues.append({**_issue("RUL_DECIMAL_CANONICALIZATION_LOSS",
                                            f"DECIMAL literal {v!r} would be released as {canonical!r}: canonicalization "
                                            "changes its exact value, so the rule cannot be published as authored.", nid),
                                   "authored_value": v, "canonical_value": canonical})
            return t
        if nt == "SET_REF":
            svid = n.get("set_version_id")
            sv = set_versions.get(svid) if isinstance(svid, str) else None
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

    root = walk(ast, 1, False)
    if root not in (None, "TRUTH3"):
        # §04: the rule is a truth expression (ALL/ANY/NOT/PREDICATE -> TRUTH3); a FactRef, Constant, SetRef or
        # ContextRef is a value. RUL-INV-04: every valid typed input must yield TRUE/FALSE/UNKNOWN, but the evaluator
        # cannot take a value as the rule (RUL_UNKNOWN_NODE on every input), so it is a static type error here (§11 stage 3).
        issues.append(_issue("RUL_TYPE_MISMATCH", f"The rule root must be a truth expression (ALL, ANY, NOT or "
                                                  f"PREDICATE), not a {root} value.",
                             ast["node_id"] if isinstance(ast.get("node_id"), str) else None))
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
        if b == "DECIMAL":  # RUL §05 "Canonical decimal string": NaN / Infinity are not decimals
            return isinstance(v, str) and "e" not in v.lower() and Decimal(v).is_finite()
        if b == "DATE":
            date.fromisoformat(v)
            return True
        if b == "TIMESTAMP":  # RUL §03: an offset-aware instant, never a local date/time (timestamp_value)
            return isinstance(v, str) and timestamp_value(v) is not None
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
        _set_domain_check(args, fact_defs, nid, issues)
    elif op in ("INTERSECTS", "CONTAINS_ALL"):
        if not (is_set(a) and is_set(types[1]) and _compatible(elem(a), elem(types[1]))):
            issues.append(_issue("RUL_TYPE_MISMATCH", f"{op} needs SET<T> x SET<T>; got {types}.", nid))
        _set_domain_check(args, fact_defs, nid, issues)
    elif op == "SET_SIZE_GTE":
        if not is_set(a) or types[1] != "INTEGER":
            issues.append(_issue("RUL_TYPE_MISMATCH", "SET_SIZE_GTE needs SET<T> x INTEGER.", nid))


def _set_domain_check(args, fact_defs, nid, issues):
    """ENUM<T> "Values must come from versioned domain T" (RUL §04), for an inline set compared with a fact, as EQ checks
    a scalar literal (_enum_domain_check): every member must belong to the fact's domain."""
    f = next((x for x in args if x.get("node_type") == "FACT_REF"), None)
    c = next((x for x in args if x.get("node_type") == "CONST" and isinstance(x.get("value"), list)), None)
    dom = fact_defs.get(f.get("fact"), {}).get("enum_domain") if f else None
    if c and dom:
        for m in c["value"]:
            if isinstance(m, str) and m not in dom:
                issues.append(_issue("RUL_TYPE_MISMATCH", f"Value {m!r} not in domain of {f['fact']}.", nid))


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


def decimal_canonicalization_loss(literal_type: str, value: Any) -> str | None:
    """The canonical form canonicalize() gives a valid DECIMAL literal when that form has a different exact value, else
    None. Owner decision (RUL owner and Head of Assurance), "DECIMAL precision-loss guard": a literal canonicalization
    would change is refused at validation (RUL_DECIMAL_CANONICALIZATION_LOSS) instead of being released changed; e.g.
    "1234567890123456789012345678.1234567890" becomes "1234567890123456789012345678" (Decimal.normalize() rounds to 28
    significant digits). The canonicalizer itself decides the canonical form (one CONST node through canonicalize, which
    is not changed), and the two values are compared with exact Decimal equality, never binary floating point. A value
    whose canonical form only changes spelling ("50000000.00" -> "5E+7") keeps its value and is not refused: exponent-form
    output is outside this decision."""
    canonical = canonicalize({"node_type": "CONST", "type": literal_type, "value": value})["value"]
    return None if Decimal(canonical) == Decimal(value) else canonical


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
def _decimal(v: Any) -> Decimal:
    """A DECIMAL operand. A value that is not a finite decimal (NaN, sNaN, +/-Infinity, e.g. a fact stored before fact
    writes refused them) is never compared: Decimal ordering with NaN raises, and equality with it is silently FALSE.
    The evaluation is refused with the type error instead (APP §12: an error, never an applicability outcome)."""
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        d = None
    if d is None or not d.is_finite():
        raise RuleError("RUL_TYPE_MISMATCH", f"{v!r} is not a finite DECIMAL value and cannot be compared.")
    return d


def _coerce(t: str, v: Any):
    b = _base(t)
    if is_set(t):
        return frozenset(_coerce(elem(t), x) for x in v)
    if b == "DECIMAL":
        return _decimal(v)
    if b == "DATE":
        # Option U (legal_date): an instant is read as its UTC calendar date, never as the first ten characters as
        # written; a malformed value (e.g. a SET<DATE> fact member never checked as a date) is refused, as NaN is.
        return legal_date(v)
    if b == "TIMESTAMP":
        # A value without a UTC offset (stored before fact writes checked TIMESTAMP values, or malformed text) is never
        # compared: against an instant Python raised TypeError (ordering) or answered FALSE (EQ / IN), an outcome
        # from an undefined comparison. Refused as a type error instead (APP §12: an error, never an outcome).
        d = timestamp_value(v)
        if d is None:
            raise RuleError("RUL_TYPE_MISMATCH", f"{v!r} is not an offset-aware TIMESTAMP instant and cannot be compared.")
        return d
    return v


class _Eval:
    def __init__(self, facts: dict, fact_types: dict, sets: dict, context: dict):
        self.facts, self.fact_types, self.sets, self.context = facts, fact_types, sets, context

    @staticmethod
    def _typed(t: str, v: Any, n: dict):
        try:
            return _coerce(t, v)
        except RuleError as e:
            raise RuleError(e.code, e.message, n.get("node_id")) from None

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
            return True, self._typed(self.fact_types[key], f["value"], n), tr
        if nt == "CONST":
            tr["display_value"] = n["value"]
            return True, self._typed(n["type"], n["value"], n), tr
        if nt == "SET_REF":
            s = self.sets.get(n["set_version_id"])
            tr["set_version_id"] = n["set_version_id"]
            if s is None:
                tr.update(result=UNKNOWN, reason_code="RULE_GAP")
                return False, None, tr
            return True, frozenset(_coerce(s["element_type"], m) for m in s["members"]), tr
        if nt == "CONTEXT_REF":
            # The legal date compared (Option U, legal_date), shown as compared: the sandbox and golden vectors pass
            # effective_at as written and live evaluation and replay pass its legal date, so the trace of one instant
            # is the same on every path (and unchanged for live evaluation, whose context is already that date).
            d = _coerce("DATE", self.context["effective_at"])
            tr["display_value"] = d.isoformat()
            return True, d, tr
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
            if single and nt != "ANY":  # no single-match semantics outside ANY (validate: RUL_SCHEMA_INVALID)
                raise RuleError("RUL_SCHEMA_INVALID", "single_match is defined only on an ANY node; an ALL node is "
                                                      "never evaluated with single-match semantics.", n["node_id"])
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
                if trues == 1 and UNKNOWN in results:
                    # RUL §07 "TRUE + UNKNOWN rows: TRUE only if exclusivity proof guarantees UNKNOWN rows cannot also
                    # legally match; otherwise ... runtime returns invariant breach." Under the compiler's proof (every
                    # pair of rows differs on an EQ column, compared by value) a matching row makes every other row
                    # FALSE, so this state means no proof covers the node: an error, never a TRUE outcome.
                    raise RuleError("RUNTIME_RULE_INVARIANT_BREACH", "SINGLE_MATCH matched one row while another row is "
                                    "UNKNOWN; exclusivity is not proven, so the match cannot be relied on.", n["node_id"])
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


# The recorded fields a rule-branch step shows: never a fact or constant value (display_value), which stays in the trace.
_BRANCH_STEP_FIELDS = ("node_id", "node_type", "result", "operator", "row_id", "reason_code", "fact", "source_mappings")


def fact_ref_paths(trace: Any, fact_key: str) -> list[dict]:
    """Every path in a recorded evaluation trace from the root to a FACT_REF node of ``fact_key``, in trace order: the
    nodes on it with their recorded result, operator, row, unknown reason and own source mappings (no value), and
    whether the fact is material on it. Material is _collect_unknown_facts' rule, the one ``evaluate`` used to list the
    evaluation's missing_facts: an UNKNOWN fact node whose ancestors, root included, are all UNKNOWN. A node the
    evaluator short-circuited has no children in the trace, so a fact it did not need has no path. Read-only."""
    out: list[dict] = []

    def walk(node, path: list, material: bool) -> None:
        if not isinstance(node, dict):
            return
        here = [*path, {k: node[k] for k in _BRANCH_STEP_FIELDS if k in node}]
        if node.get("node_type") == "FACT_REF":
            if node.get("fact") == fact_key:
                out.append({"path": here, "material": material and node.get("result") == UNKNOWN})
            return
        for c in node.get("children") or []:
            walk(c, here, material and node.get("result") == UNKNOWN)
    if isinstance(trace, dict):
        walk(trace, [], trace.get("result") == UNKNOWN)
    return out


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


def _fact_value_ok(fd: dict, value: Any) -> bool:
    """A typed fact input as FactDefinition accepts it (facts.typed_columns: no implicit coercion), and one the
    evaluator can execute: a DECIMAL finite and held exactly by value_decimal (decimal_value_ok), a DATE / TIMESTAMP
    an ISO string."""
    t, dom = fd.get("data_type") or "", fd.get("enum_domain")
    try:
        if t == "BOOLEAN":
            return isinstance(value, bool)
        if t == "INTEGER":
            return isinstance(value, int) and not isinstance(value, bool) and -(2 ** 63) <= value < 2 ** 63
        if t == "DECIMAL":
            return not isinstance(value, bool) and decimal_value_ok(Decimal(str(value)))
        if t == "DATE":
            return isinstance(value, str) and date.fromisoformat(value) is not None
        if t == "TIMESTAMP":  # an offset-aware instant, as fact writes and the evaluator require (timestamp_value)
            return isinstance(value, str) and timestamp_value(value) is not None
        if is_set(t):
            return isinstance(value, list) and all(isinstance(x, str) for x in value) and \
                not (dom and any(x not in dom for x in value))
        return isinstance(value, str) and not (dom and value not in dom)  # ENUM, STRING, COUNTRY, JURISDICTION
    except (ValueError, InvalidOperation, TypeError):
        return False


def vector_input_problems(vec: Any, fact_defs: dict) -> list[dict]:
    """Structural and type problems of one golden vector or sandbox fixture, found before it is evaluated (ZA-RUL-001
    §13: "facts: Typed inputs or deliberate UNKNOWN reason"; expected_outcome APPLIES / DOES_NOT_APPLY / UNCERTAIN;
    trace_assertions keyed by node ID; an explicit effective_at). Each problem carries its §18 code: RUL_SCHEMA_INVALID
    for a malformed field, RUL_FACT_REF_INVALID for a fact key with no FactDefinition, RUL_TYPE_MISMATCH for a value
    the FactDefinition does not accept. A vector with any problem is never evaluated and never passes."""
    def p(code, field, msg, **kw):
        return {"code": code, "severity": "ERROR", "field": field, "message": msg, **kw}
    if not isinstance(vec, dict):
        return [p("RUL_SCHEMA_INVALID", None, "A golden vector must be an object.")]
    out = []
    if vec.get("expected_outcome") not in OUTCOME.values():
        out.append(p("RUL_SCHEMA_INVALID", "expected_outcome",
                     f"expected_outcome must be one of {sorted(OUTCOME.values())}, not {vec.get('expected_outcome')!r}."))
    if vec.get("vector_class") is not None and not isinstance(vec.get("vector_class"), str):
        out.append(p("RUL_SCHEMA_INVALID", "vector_class", "vector_class must be a string."))
    ta = vec.get("trace_assertions")
    if ta is not None and not (isinstance(ta, dict) and all(isinstance(k, str) for k in ta)):
        out.append(p("RUL_SCHEMA_INVALID", "trace_assertions", "trace_assertions must map node IDs to expected states."))
    # RUL §13 "effective_at / record_cutoff: Explicit time perspective" (WF-RUL-04 "Time controls: effective_at ...
    # explicit"): a vector or fixture without one is refused like a malformed one, never run at an assumed date.
    if "effective_at" not in vec:
        out.append(p("RUL_SCHEMA_INVALID", "effective_at",
                     "effective_at is required: the time perspective is stated explicitly (ZA-RUL-001 §13); no date "
                     "is assumed."))
    elif not _effective_at_ok(vec["effective_at"]):
        out.append(p("RUL_SCHEMA_INVALID", "effective_at", f"effective_at {vec['effective_at']!r} is not an ISO date."))
    return out + fact_input_problems(vec.get("facts"), fact_defs)


def _effective_at_ok(ea: Any) -> bool:
    """A golden-vector or sandbox effective_at (RUL §13 "Explicit time perspective"): the whole string is an ISO 8601 date
    or date-time. Only the date part used to be checked, so trailing text ("2027-01-01garbage") was accepted and silently
    ignored. Which values are accepted is unchanged; the legal date the evaluator reads from one is legal_date's (Option
    U: the UTC calendar date of an instant with an offset, the written date of a date or of a date-time without one).
    An instant with no UTC calendar date (legal_date refuses it) is refused here too: accepted, it failed evaluation with
    an unhandled error instead of being reported as a malformed input."""
    if not isinstance(ea, str):
        return False
    try:
        date.fromisoformat(ea[:10])
    except ValueError:
        return False
    try:
        date.fromisoformat(ea)
        return True
    except ValueError:
        pass
    try:
        datetime.fromisoformat(ea.replace("Z", "+00:00"))
    except ValueError:
        return False
    try:
        legal_date(ea)
    except RuleError:
        return False
    return True


def fact_input_problems(facts: Any, fact_defs: dict) -> list[dict]:
    """The `facts` of a golden vector or sandbox fixture: an object of FactDefinition key -> typed value, or
    {"unknown": "<reason>"} for a deliberate UNKNOWN (see vector_input_problems)."""
    def p(code, fact, msg):
        return {"code": code, "severity": "ERROR", "field": "facts", "fact": fact, "message": msg}
    if facts is None:
        return []
    if not isinstance(facts, dict):
        return [p("RUL_SCHEMA_INVALID", None, "facts must be an object of fact key to typed value.")]
    out = []
    for key, val in facts.items():
        fd = fact_defs.get(key)
        if fd is None:
            out.append(p("RUL_FACT_REF_INVALID", key, f"Unknown FactDefinition {key!r}."))
        elif isinstance(val, dict) and "unknown" in val:
            if not isinstance(val["unknown"], str) or not val["unknown"].strip():
                out.append(p("RUL_SCHEMA_INVALID", key, f"The UNKNOWN reason for {key} must be a non-empty string."))
        elif not _fact_value_ok(fd, val):
            out.append(p("RUL_TYPE_MISMATCH", key, f"Value {val!r} is not a valid {fd.get('data_type')} for fact {key}"
                         + (f" (domain {fd.get('enum_domain')})." if fd.get("enum_domain") else ".")))
    return out


def run_golden_vectors(ast: dict, vectors: list[dict], fact_defs: dict, sets: dict) -> dict:
    """Execute reviewed golden vectors against exact AST (RUL-REQ-017). A vector whose input is malformed
    (vector_input_problems) is a failed result carrying its first §18 code and every problem, never evaluated."""
    ftypes = {k: v["data_type"] for k, v in fact_defs.items()}
    results = []
    if not isinstance(vectors, list):
        vectors = [vectors]
    for vec in vectors:
        problems = vector_input_problems(vec, fact_defs)
        if problems:
            results.append({"vector_id": vec.get("vector_id") if isinstance(vec, dict) else None, "passed": False,
                            "error": problems[0]["code"], "message": problems[0]["message"], "issues": problems})
            continue
        facts = {}
        for k, val in (vec.get("facts") or {}).items():
            if isinstance(val, dict) and "unknown" in val:
                facts[k] = {"known": False, "reason": val["unknown"]}
            else:
                facts[k] = {"known": True, "value": val, "revision_id": "golden"}
        try:
            # The vector's own time perspective, present and well formed (vector_input_problems above); none is assumed.
            out = evaluate(ast, facts, ftypes, sets, {"effective_at": vec["effective_at"]})
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
    classes = {v.get("vector_class") for v in vectors if isinstance(v, dict) and isinstance(v.get("vector_class"), str)}
    missing = sorted({"POSITIVE", "NEGATIVE", "UNKNOWN"} - classes)
    # The build these vectors just ran on (RUL §11 stage 9 "on exact evaluator build"; §17 /test "Results linked to draft
    # revision/evaluator build"). A copy of the identifier here would keep naming the old build after a build change, so a
    # changed evaluator's results would be attributed to the build that preceded it.
    return {"total": len(results), "passed": passed, "failed": len(results) - passed,
            "all_passed": bool(results) and passed == len(results), "results": results,
            "missing_mandatory_classes": missing, "evaluator_build": codes.EVALUATOR_BUILD}


def _find(trace: dict, node_id: str) -> dict | None:
    if trace.get("node_id") == node_id:
        return trace
    for c in trace.get("children", []) or []:
        f = _find(c, node_id)
        if f:
            return f
    return None
