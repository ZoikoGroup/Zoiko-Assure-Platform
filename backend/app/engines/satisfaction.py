"""SatisfactionExpression authoring and control mappings (ZA-STA-001 §07; ZA-DOM-001 §7.1, §9, §13).

Control mappings. DOM §7.1 defines ControlMapping as the link from an obligation expression leaf "to control
implementation or control role", "many-to-many and effective-dated"; DOM §9.1 makes it bitemporal and DOM §9.2 says a
corrected mapping is a new revision while "prior snapshots continue referencing prior mapping revisions used at
computation time". ZA-DAT-001 defines no separate control-mapping table, so a mapping is carried by the leaf of an
immutable SatisfactionExpressionVersion (DAT §07: "AST/hash, valid/record time, rationale/review refs"):

  * control role (selector): ``{"node_type": "CONTROL", "control_key": K}``; resolved before evaluation to the
    obligation subject's implementation of K, else its legal entity's (STA §07: "control selector resolved before
    evaluation"; engines/assurance.py _resolve_impl);
  * exact implementation: the same leaf plus ``"control_implementation_id"`` (STA §07: "Leaf referring to exact
    ControlImplementation").

A mapping is created, changed or retired only by a new expression version, so every snapshot keeps the exact version
and mappings it used (STA §07 "Expression versioning"; STA-REQ-021). Versions are never updated.

Authority (STA §22): "Change SatisfactionExpression: Control Owner propose input; Assurance Reviewer approve design;
Legal approve legal mapping where material; System/AI no autonomous release". A version is released by a human holding
``assurance_review`` (ZA-IAM-001 ASSURANCE_REVIEWER). A persisted propose/approve step and the legal-materiality approval
have no table or status in migrations 0001-0013, so they are not modelled here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from ..models import (Compensation, ControlDefinition, ControlImplementation, ObligationInstance,
                      SatisfactionExpressionVersion, Subject)
from ..platform import audit, digest, ensure_utc, next_record_seq
from .assurance import expression_problems
from .locks import xact_lock

# STA §07 "Required metadata" for an authored expression: CONTROL "control ref, requirement rationale, required=true";
# ANY "Legal/control rationale explaining interchangeability/alternatives"; K_OF_N "K, N, rationale".
RATIONALE_NODES = ("CONTROL", "ANY", "K_OF_N")


def scope_subject_ids(db, tenant_id: str, subject: Subject) -> list[str]:
    """The subjects whose control implementations may satisfy this obligation's leaves: the obligation subject and its
    legal entity (the same scope the selector resolves in engines/assurance.py _resolve_impl)."""
    ids = [subject.subject_id]
    if subject.entity_id:
        ent = db.execute(select(Subject).where(Subject.tenant_id == tenant_id, Subject.entity_id == subject.entity_id,
                                               Subject.subject_type == "LEGAL_ENTITY")
                         .order_by(Subject.subject_id)).scalars().first()
        if ent and ent.subject_id not in ids:
            ids.append(ent.subject_id)
    return ids


def binding_problem(db, tenant_id: str, subject: Subject, control_key: str, impl_id: str) -> str | None:
    """Why an exact leaf binding is not a valid mapping for this obligation (None: it is)."""
    ci = db.get(ControlImplementation, impl_id)
    if ci is None or ci.tenant_id != tenant_id:  # another organisation's row is reported as not found
        return f"control implementation {impl_id} not found"
    cd = db.get(ControlDefinition, ci.control_definition_id)
    if cd is None or cd.key != control_key:
        return f"control implementation {impl_id} implements {cd.key if cd else '?'}, not {control_key}"
    if ci.subject_id not in scope_subject_ids(db, tenant_id, subject):
        return f"control implementation {impl_id} is outside the obligation's subject scope"
    return None


def leaves(node, path: str = "$") -> list[tuple[str, dict]]:
    """Every CONTROL leaf reachable through children / primary / compensating, with its path."""
    if not isinstance(node, dict):
        return []
    out = [(path, node)] if node.get("node_type") == "CONTROL" else []
    for i, c in enumerate(node.get("children") or []):
        out += leaves(c, f"{path}.children[{i}]")
    for part in ("primary", "compensating"):
        if isinstance(node.get(part), dict):
            out += leaves(node[part], f"{path}.{part}")
    return out


def _nodes(node, path="$"):
    if not isinstance(node, dict):
        return []
    out = [(path, node)]
    for i, c in enumerate(node.get("children") or []):
        out += _nodes(c, f"{path}.children[{i}]")
    for part in ("primary", "compensating"):
        if isinstance(node.get(part), dict):
            out += _nodes(node[part], f"{path}.{part}")
    return out


def _canonical(node):
    """A node without display metadata, to compare an authored COMPENSATION branch with the approved one."""
    if not isinstance(node, dict):
        return node
    out = {k: v for k, v in node.items() if k not in ("node_id", "rationale")}
    if "children" in out:
        out["children"] = [_canonical(c) for c in out["children"]]
    for part in ("primary", "compensating"):
        if part in out:
            out[part] = _canonical(out[part])
    return out


def metadata_problems(ast, *, bindings_allowed: bool) -> list[dict]:
    """ZA-STA-001 §07 rules shared by catalogue SatisfactionExpression templates (catalog.validate_template) and
    organisation expression versions (authoring_problems): unique node IDs, the "Required metadata" (rationale on
    CONTROL / ANY / K_OF_N outside a carried COMPENSATION branch; CONTROL required=true), distinct sibling leaves.
    ``bindings_allowed=False`` (global catalogue content): a CONTROL leaf names a control role only; an exact
    control_implementation_id would make shared content reference one organisation's record (DOM-INV-11)."""
    out: list[dict] = []
    nodes = _nodes(ast)
    for path, n in nodes:
        if not isinstance(n.get("node_id"), str) or not n["node_id"].strip():
            out.append({"code": "NODE_ID_REQUIRED", "path": path, "message": "every node needs a node_id"})
        if (n["node_type"] in RATIONALE_NODES and not _inside_compensation(path)
                and not (isinstance(n.get("rationale"), str) and n["rationale"].strip())):
            out.append({"code": "RATIONALE_REQUIRED", "path": path,
                        "message": f"{n['node_type']} needs a rationale (ZA-STA-001 §07 required metadata)"})
        if n["node_type"] == "CONTROL" and "required" in n and n["required"] is not True:
            out.append({"code": "CONTROL_NOT_REQUIRED", "path": path,
                        "message": "a CONTROL leaf is required=true; use OPTIONAL_EVIDENCE for non-binding information"})
        if n["node_type"] == "CONTROL" and not bindings_allowed and "control_implementation_id" in n:
            out.append({"code": "CONTROL_BINDING_NOT_ALLOWED", "path": path,
                        "message": "catalogue content maps control roles only; an exact control implementation belongs "
                                   "to one organisation's expression version (DOM-INV-11)"})
        if n["node_type"] in ("ALL", "ANY", "K_OF_N"):
            keys = [c.get("control_key") for c in n.get("children") or [] if isinstance(c, dict)
                    and c.get("node_type") == "CONTROL"]
            dup = sorted({k for k in keys if keys.count(k) > 1})
            if dup:
                out.append({"code": "DUPLICATE_CONTROL_LEAF", "path": path,
                            "message": f"{n['node_type']} lists {dup} more than once; peers must be distinct"})
    ids = [n.get("node_id") for _, n in nodes]
    dup_ids = sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1})
    if dup_ids:
        out.append({"code": "DUPLICATE_NODE_ID", "path": None, "message": f"node_id(s) used more than once: {dup_ids}"})
    return out


def control_roles(db, ast) -> list[dict]:
    """Requirement-to-control traceability: each CONTROL leaf of an expression or template with its control definition
    (the control role) and, for an organisation's version, its exact implementation binding."""
    out = []
    for path, n in leaves(ast):
        cd = db.execute(select(ControlDefinition).where(ControlDefinition.key == n["control_key"])).scalars().first()
        out.append({"path": path, "node_id": n.get("node_id"), "control_key": n["control_key"],
                    "control_definition_id": cd.control_definition_id if cd else None,
                    "title": cd.title if cd else None, "known": cd is not None,
                    "binding": "EXACT" if n.get("control_implementation_id") else "SELECTOR"})
    return out


def in_force_at(versions, at: datetime):
    """The version the evaluator would use at ``at`` (engines/assurance.py compute_snapshot in_force): the highest
    version whose valid period contains the instant. None when no version is in force."""
    at = ensure_utc(at)
    cands = [v for v in versions if ensure_utc(v.valid_from) <= at and (v.valid_to is None or ensure_utc(v.valid_to) > at)]
    return max(cands, key=lambda v: v.version) if cands else None


def authoring_problems(db, *, tenant_id: str, lineage_id: str, subject: Subject, ast) -> list[dict]:
    """Every reason an authored expression cannot become a version (empty list: it can), as {code, path, message}."""
    out = [{"code": "EXPRESSION_STRUCTURE_INVALID", "path": None, "message": m} for m in expression_problems(ast)]
    if out:
        return out
    out += metadata_problems(ast, bindings_allowed=True)
    nodes = _nodes(ast)
    found = leaves(ast)
    keys = sorted({n["control_key"] for _, n in found})
    known = {k for (k,) in db.execute(select(ControlDefinition.key).where(ControlDefinition.key.in_(keys or ["-"])))}
    for path, n in found:
        if n["control_key"] not in known:
            out.append({"code": "UNKNOWN_CONTROL_DEFINITION", "path": path,
                        "message": f"{n['control_key']} is not a catalogue control definition"})
    bound: dict[str, set] = {}
    for path, n in found:
        impl = n.get("control_implementation_id")
        bound.setdefault(n["control_key"], set()).add(impl)
        if impl is None:
            continue
        if not isinstance(impl, str):
            out.append({"code": "CONTROL_MAPPING_INVALID", "path": path, "message": "control_implementation_id must be a string"})
            continue
        problem = binding_problem(db, tenant_id, subject, n["control_key"], impl) if n["control_key"] in known else None
        if problem:
            out.append({"code": "CONTROL_MAPPING_INVALID", "path": path, "message": problem})
    for key, impls in sorted(bound.items()):
        if len(impls) > 1:  # one control role resolved two ways in one version: a contradictory mapping
            out.append({"code": "CONTRADICTORY_CONTROL_MAPPING", "path": None,
                        "message": f"{key} is mapped to more than one implementation or both exactly and by selector"})
    out += _compensation_node_problems(db, tenant_id, lineage_id, nodes)
    return out


def _inside_compensation(path: str) -> bool:
    return ".primary" in path or ".compensating" in path


def _compensation_node_problems(db, tenant_id, lineage_id, nodes) -> list[dict]:
    """A COMPENSATION node is created only by an approved compensation (governance.decide_compensation). An authored
    version may carry one forward unchanged; it cannot introduce, alter or revive one (STA §09; STA-REQ-013)."""
    out = []
    for path, n in nodes:
        if n["node_type"] != "COMPENSATION":
            continue
        c = db.get(Compensation, n.get("compensation_id"))
        if c is None or c.tenant_id != tenant_id or c.obligation_lineage_id != lineage_id:
            out.append({"code": "COMPENSATION_NODE_INVALID", "path": path,
                        "message": "the COMPENSATION node names no compensation of this obligation"})
        elif c.status != "APPROVED":
            out.append({"code": "COMPENSATION_NODE_INVALID", "path": path,
                        "message": f"compensation {c.compensation_id} is {c.status}, not APPROVED"})
        elif (n["primary"].get("control_key") != c.primary_control_key
              or _canonical(n["compensating"]) != _canonical(c.compensating_expression)):
            out.append({"code": "COMPENSATION_NODE_INVALID", "path": path,
                        "message": "the COMPENSATION node does not match its approved compensation"})
    return out


def _summary(ast) -> dict[str, str | None]:
    return {n["control_key"]: n.get("control_implementation_id") for _, n in leaves(ast)}


def mapping_diff(prev_ast, new_ast) -> dict:
    """What a new version does to the control mappings of the previous one (for the audit record)."""
    before, after = _summary(prev_ast) if prev_ast else {}, _summary(new_ast)
    return {"added": sorted(set(after) - set(before)), "retired": sorted(set(before) - set(after)),
            "rebound": sorted(k for k in set(before) & set(after) if before[k] != after[k])}


def hash_intact(e: SatisfactionExpressionVersion) -> bool:
    """Whether a version's stored AST still hashes to the hash recorded when it was released. Versions are immutable
    (STA-REQ-019) but the database does not yet enforce it (the append-only migration awaits approval), so a version
    edited in place is detected here and never used as if it were the released one (STA-REQ-021)."""
    try:
        return digest(e.ast) == e.ast_hash
    except (TypeError, ValueError):
        return False


def view(db, e: SatisfactionExpressionVersion) -> dict:
    return {"integrity": {"ast_hash_matches": hash_intact(e)},"expression_version_id": e.expression_version_id, "obligation_lineage_id": e.obligation_lineage_id,
            "version": e.version, "ast": e.ast, "ast_hash": e.ast_hash, "rationale": e.rationale,
            "valid_from": e.valid_from, "valid_to": e.valid_to, "record_seq": e.record_seq,
            "control_mappings": [{"path": p, "node_id": n.get("node_id"), "control_key": n["control_key"],
                                  "binding": "EXACT" if n.get("control_implementation_id") else "SELECTOR",
                                  "control_implementation_id": n.get("control_implementation_id")}
                                 for p, n in leaves(e.ast)]}


def versions(db, tenant_id: str, lineage_id: str) -> list[SatisfactionExpressionVersion]:
    return db.execute(select(SatisfactionExpressionVersion).where(
        SatisfactionExpressionVersion.tenant_id == tenant_id,
        SatisfactionExpressionVersion.obligation_lineage_id == lineage_id)
        .order_by(SatisfactionExpressionVersion.version, SatisfactionExpressionVersion.expression_version_id)).scalars().all()


def create_version(ctx, *, lineage_id: str, ast: dict, valid_from: datetime, valid_to: datetime | None,
                   rationale: str) -> SatisfactionExpressionVersion:
    """Release a new SatisfactionExpressionVersion for one obligation: validate, number, record and audit it in one
    transaction. Earlier versions, and the snapshots that pinned them, are never changed."""
    db, tenant = ctx.db, ctx.tenant
    # One version change at a time per obligation (the same key compensation approval takes), then read the state.
    xact_lock(db, "satisfaction-expression", tenant, lineage_id)
    obl = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant,
                                                      ObligationInstance.lineage_id == lineage_id,
                                                      ObligationInstance.record_to_seq.is_(None))).scalars().first()
    if obl is None:
        raise HTTPException(404, "obligation not found")
    if valid_to is not None and ensure_utc(valid_to) <= ensure_utc(valid_from):
        raise HTTPException(422, {"error": "EXPRESSION_PERIOD_EMPTY",
                                  "message": "valid_to must be later than valid_from (half-open, non-empty)"})
    problems = authoring_problems(db, tenant_id=tenant, lineage_id=lineage_id, subject=db.get(Subject, obl.subject_id),
                                  ast=ast)
    if problems:
        raise HTTPException(422, {"error": problems[0]["code"], "message": "; ".join(p["message"] for p in problems[:10]),
                                  "problems": problems})
    existing = versions(db, tenant, lineage_id)
    h = digest(ast)
    if existing:
        latest = max(existing, key=lambda e: e.version)
        if latest.ast_hash == h and ensure_utc(latest.valid_from) == ensure_utc(valid_from) and \
                (latest.valid_to is None) == (valid_to is None) and \
                (valid_to is None or ensure_utc(latest.valid_to) == ensure_utc(valid_to)):
            raise HTTPException(409, {"error": "EXPRESSION_UNCHANGED", "message": "the latest version already says this",
                                      "expression_version_id": latest.expression_version_id})
    number = (db.execute(select(func.max(SatisfactionExpressionVersion.version)).where(
        SatisfactionExpressionVersion.tenant_id == tenant,
        SatisfactionExpressionVersion.obligation_lineage_id == lineage_id)).scalar() or 0) + 1
    seq = next_record_seq(db)
    e = SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage_id, version=number, ast=ast,
                                      ast_hash=h, rationale=rationale.strip(), valid_from=valid_from, valid_to=valid_to,
                                      record_seq=seq)
    db.add(e)
    db.flush()
    prev = max(existing, key=lambda x: x.version) if existing else None
    diff = mapping_diff(prev.ast if prev else None, ast)
    retroactive = ensure_utc(valid_from) < datetime.now(timezone.utc)
    audit(db, action="satisfaction_expression.version_created", object_type="SatisfactionExpressionVersion",
          object_id=e.expression_version_id, actor=ctx.actor, tenant_id=tenant, record_seq=seq,
          reason=(f"obligation {lineage_id} version {number} (previous {prev.version if prev else None}); valid from "
                  f"{ensure_utc(valid_from).isoformat()}{' (retroactive)' if retroactive else ''}; mappings added "
                  f"{diff['added']} retired {diff['retired']} rebound {diff['rebound']}; hash {h[:16]}; "
                  f"{rationale.strip()}")[:2000])
    return e
