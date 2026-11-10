"""Assurance state machines & period roll-up (ZA-STA-001) over bitemporal segments (ZA-TIM-001 §9).

* Control state precedence: FAILING > REQUIRES_REVIEW > NOT_EVALUATED > DEGRADED > OPERATING.
* Internal four values SATISFIED / UNSATISFIED / UNKNOWN / REVIEW (never UI states).
* Expression nodes: CONTROL, ALL, ANY, K_OF_N, COMPENSATION, OPTIONAL_EVIDENCE, OCCURRENCES.
* Period: FAILING > REQUIRES_REVIEW > NOT_PROVEN > PROVEN; every boundary creates a segment.
* Exceptions are reported separately and never change any state (STA-REQ-014).
* Inputs are read in record order (record_seq, then id; DAT-REQ-007, TIM-REQ-005) and within the evaluated tenant, so
  digests, traces and selections do not depend on physical row order or on RLS (STA-REQ-020/021; roadmap Stage 3a).
  Test definitions carry no record order and keep their stored order (roadmap §6, B1).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import codes
from ..models import (AssuranceSnapshot, Compensation, ControlDefinition, ControlEvaluation, ControlImplementation,
                      ControlTestPolicy, EvidenceArtifact, EvidenceConflict, ExceptionRecord, Issue,
                      ObligationDefinition, ObligationInstance, ObligationOccurrence, SatisfactionExpressionVersion,
                      SourceRun, Subject, TestDefinition, TestRun)
from ..platform import digest, emit, ensure_utc, next_record_seq
from . import evidence as evd
from . import ipe

log = logging.getLogger("zoiko.assurance")

# ZA-STA-001 §03, "ControlTestPolicy aggregation v1" and "AutomationRequirement". CUSTOM_VERSIONED_POLICY is a v1 mode
# that this evaluator does not implement ("only when expressed through the same constrained test-policy AST and approved
# through Assurance governance"): it is refused explicitly rather than evaluated as another mode.
AGGREGATION_MODES = ("ALL_REQUIRED", "K_OF_N_TESTS", "PRIMARY_PLUS_CORROBORATION")
UNSUPPORTED_AGGREGATION_MODES = ("CUSTOM_VERSIONED_POLICY",)
AUTOMATION_REQUIREMENTS = ("NONE", "PREFERRED", "REQUIRED")
EXPRESSION_NODES = ("CONTROL", "ALL", "ANY", "K_OF_N", "COMPENSATION", "OPTIONAL_EVIDENCE", "OCCURRENCES")


class AssuranceConfigurationError(HTTPException):
    """A control test policy or SatisfactionExpression that cannot be evaluated. ZA-STA-001 §16: errors are not business
    states, and an invalid SatisfactionExpression "is a release/configuration error and must be blocked before
    production use"; STA-REQ-022. Raised instead of returning OPERATING, FAILING, NOT_PROVEN or any other state, so no
    evaluation, snapshot or issue is written from it. HTTP 409 with a stable code, as for a missing expression."""

    def __init__(self, code: str, message: str, **refs):
        super().__init__(409, {"error": "ASSURANCE_CONFIGURATION_INVALID", "code": code, "message": message, **refs})
        log.warning("assurance configuration invalid: %s %s %s", code, message, refs)  # identifiers only, no evidence


def policy_problems(aggregation_mode, k_threshold, automation_requirement, tests) -> list[str]:
    """Why a control test policy cannot be evaluated (empty list: it can). ``tests`` are (required, primary) pairs.
    Shared by the API boundary (routers/assurance.py create_control) and the evaluator, so stored data that predates
    the API check is refused at evaluation as well."""
    out = []
    if aggregation_mode in UNSUPPORTED_AGGREGATION_MODES:
        out.append(f"aggregation_mode {aggregation_mode} is not supported by this evaluator (ZA-STA-001 §03 requires the "
                   "constrained test-policy AST approved through Assurance governance)")
    elif aggregation_mode not in AGGREGATION_MODES:
        out.append(f"aggregation_mode must be one of {list(AGGREGATION_MODES)} (ZA-STA-001 §03)")
    if automation_requirement not in AUTOMATION_REQUIREMENTS:
        out.append(f"automation_requirement must be one of {list(AUTOMATION_REQUIREMENTS)} (ZA-STA-001 §03)")
    required = [t for t in tests if t[0]]
    if tests and not required:
        out.append("a policy with tests needs at least one required test; optional tests alone cannot prove a control "
                   "(STA-REQ-006)")
    if aggregation_mode == "K_OF_N_TESTS":
        k = k_threshold
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(required):
            out.append(f"K_OF_N_TESTS needs an integer k_threshold between 1 and the number of required tests "
                       f"({len(required)}); got {k!r}")
    elif k_threshold is not None:
        out.append("k_threshold applies only to K_OF_N_TESTS")
    if aggregation_mode == "PRIMARY_PLUS_CORROBORATION" and len([t for t in required if t[1]]) != 1:
        out.append("PRIMARY_PLUS_CORROBORATION needs exactly one named primary test among the required tests "
                   "(ZA-STA-001 §03: 'Named primary test must pass')")
    return out


def expression_problems(node, path: str = "$", count: list | None = None) -> list[str]:
    """Structural validation of a SatisfactionExpression AST (STA-REQ-009/010/011, STA §09). Every ALL / ANY / K_OF_N
    needs at least one binding child (OPTIONAL_EVIDENCE is not binding), K_OF_N needs 1 <= k <= binding children, and
    COMPENSATION needs a primary, a non-empty compensating branch and a compensation_id. Control-key existence is
    checked where keys are introduced (catalog.validate_template, governance.propose_compensation)."""
    count = count if count is not None else [0]
    count[0] += 1
    if count[0] > 500:
        return ["expression exceeds 500 nodes"]
    if not isinstance(node, dict) or node.get("node_type") not in EXPRESSION_NODES:
        return [f"{path}: node_type must be one of {list(EXPRESSION_NODES)}"]
    nt, out = node["node_type"], []
    if nt == "CONTROL":
        if not isinstance(node.get("control_key"), str) or not node["control_key"]:
            out.append(f"{path}: CONTROL needs a control_key")
        if "control_implementation_id" in node and not (isinstance(node["control_implementation_id"], str)
                                                        and node["control_implementation_id"]):
            out.append(f"{path}: control_implementation_id must be a non-empty string when present")
    elif nt in ("ALL", "ANY", "K_OF_N"):
        kids = node.get("children")
        if not isinstance(kids, list):
            return [f"{path}: {nt} needs a children list"]
        for i, c in enumerate(kids):
            out += expression_problems(c, f"{path}.children[{i}]", count)
        binding = [c for c in kids if isinstance(c, dict) and c.get("node_type") != "OPTIONAL_EVIDENCE"]
        if not binding:
            out.append(f"{path}: {nt} needs at least one binding child; an empty {nt} proves nothing")
        if nt == "K_OF_N":
            k = node.get("k")
            if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(binding):
                out.append(f"{path}: K_OF_N needs an integer k between 1 and {len(binding)}; got {k!r}")
    elif nt == "COMPENSATION":
        if not node.get("compensation_id"):
            out.append(f"{path}: COMPENSATION needs a compensation_id")
        for part in ("primary", "compensating"):
            if not isinstance(node.get(part), dict):
                out.append(f"{path}: COMPENSATION needs a {part} branch")
            else:
                out += expression_problems(node[part], f"{path}.{part}", count)
        comp = node.get("compensating")
        if isinstance(comp, dict) and comp.get("node_type") == "OPTIONAL_EVIDENCE":
            out.append(f"{path}.compensating: a compensating branch must contain controls (ZA-STA-001 §09)")
    return out


SAT, UNSAT, UNK, REV = "SATISFIED", "UNSATISFIED", "UNKNOWN", "REVIEW"
LEAF_MAP = {"OPERATING": SAT, "FAILING": UNSAT, "NOT_EVALUATED": UNK, "DEGRADED": UNK, "REQUIRES_REVIEW": REV}
SEGMENT_STATE = {SAT: "PROVEN", UNSAT: "FAILING", UNK: "NOT_PROVEN", REV: "REQUIRES_REVIEW"}
PERIOD_ORDER = ["FAILING", "REQUIRES_REVIEW", "NOT_PROVEN", "PROVEN"]


def _in(dt_from, dt_to, a, b) -> bool:
    """Does [dt_from, dt_to) fully cover [a, b)? None = open."""
    return ensure_utc(dt_from) <= a and (dt_to is None or ensure_utc(dt_to) >= b)


def _overlap(f, t, a, b) -> bool:
    return ensure_utc(f) < b and (t is None or ensure_utc(t) > a)


# ------------------------------------------------------------------ control evaluation
def _ipe_overall(db: Session, source_run_id: str | None, cutoff_seq: int) -> str | None:
    """The run's bound IPE result (ipe.bound_evaluation), never the newest evaluation: a later re-evaluation, or one
    under a weaker policy, cannot turn a failed population proof into a verified one. A run recorded after the cutoff
    was not known then, so it supports nothing (None counts as IPE_INCONCLUSIVE below)."""
    if not source_run_id:
        return None
    run = db.get(SourceRun, source_run_id)
    if run is None or run.record_seq > cutoff_seq:
        return None
    ev = ipe.bound_evaluation(db, run)
    return ev.overall_result if ev else None


def _evidence_admissible(db, run: TestRun, td: TestDefinition, policy: ControlTestPolicy, a, b, cutoff_seq):
    """Returns (status, reasons, evidence_rows): status OK | CONFLICT | UNKNOWN."""
    reasons, rows = [], []
    if not run.evidence_ids:
        return UNK, ["MISSING_EVIDENCE"], rows
    arts = [db.get(EvidenceArtifact, e) for e in run.evidence_ids]
    arts = [x for x in arts if x is not None and x.tenant_id == run.tenant_id and x.record_seq <= cutoff_seq]
    if not arts:
        return UNK, ["MISSING_EVIDENCE"], rows
    conflict = False
    manual_only = True
    for art in arts:
        lc = evd.lifecycle(db, art)
        cond = "CURRENT"
        if lc == "ERASED":
            cond = "ERASED"
        elif lc == "INVALIDATED":
            cond = "INVALID"
        elif ensure_utc(art.valid_until) <= a:
            cond = "STALE"
        open_conf = db.execute(select(EvidenceConflict).where(EvidenceConflict.tenant_id == art.tenant_id,
                                                              EvidenceConflict.status == "OPEN")).scalars().all()
        if any(art.evidence_id in c.member_evidence_ids for c in open_conf):
            cond = "CONFLICTING"
            conflict = True
        if art.origin_class not in ("HUMAN_UPLOAD", "ATTESTATION"):
            manual_only = False
        ipe = _ipe_overall(db, art.source_run_id or run.source_run_id, cutoff_seq) if td.ipe_required else None
        rows.append({"evidence_id": art.evidence_id, "condition": cond, "origin_class": art.origin_class,
                     "provenance_class": art.provenance_class, "ipe_result": ipe, "valid_until": art.valid_until})
        if cond in ("STALE", "INVALID", "ERASED"):
            reasons.append({"STALE": "STALE_EVIDENCE", "INVALID": "INVALID_EVIDENCE", "ERASED": "MISSING_EVIDENCE"}[cond])
        if td.ipe_required and ipe != "IPE_VERIFIED":
            reasons.append("IPE_FAILED" if ipe == "IPE_FAILED" else "IPE_INCONCLUSIVE")
    if policy.automation_requirement == "REQUIRED" and manual_only:
        reasons.append("MANUAL_EVIDENCE_INSUFFICIENT")
    if conflict:
        return "CONFLICT", sorted(set(reasons + ["SOURCE_CONFLICT"])), rows
    return ("UNKNOWN" if reasons else "OK"), sorted(set(reasons)), rows


def evaluate_control(db: Session, ci: ControlImplementation, a: datetime, b: datetime, cutoff_seq: int,
                     persist: bool = True) -> dict:
    a, b = ensure_utc(a), ensure_utc(b)
    if ci.record_seq > cutoff_seq or not _in(ci.valid_from, ci.valid_to, a, b):
        return {"state": "NOT_EVALUATED", "reason_codes": ["CONTROL_NOT_IMPLEMENTED"], "tests": [],
                "precedence_rule": "3:NOT_EVALUATED", "evaluation_id": None}
    policy = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.tenant_id == ci.tenant_id,
                                                        ControlTestPolicy.control_implementation_id == ci.control_implementation_id,
                                                        ControlTestPolicy.record_seq <= cutoff_seq)
                        .order_by(ControlTestPolicy.version.desc(), ControlTestPolicy.policy_version_id)).scalars().first()
    if not policy:
        return {"state": "NOT_EVALUATED", "reason_codes": ["TEST_NOT_RUN"], "tests": [],
                "precedence_rule": "3:NOT_EVALUATED", "evaluation_id": None}
    # Stored order, deliberately: test definitions have no record_seq, and any other order would change this digest for
    # multi-test policies, an engine change STA-REQ-032 reserves to an ADR (roadmap §6, B1).
    tds = db.execute(select(TestDefinition).where(TestDefinition.tenant_id == ci.tenant_id,
                                                  TestDefinition.policy_version_id == policy.policy_version_id)).scalars().all()
    problems = policy_problems(policy.aggregation_mode, policy.k_threshold, policy.automation_requirement,
                               [(td.required, td.primary) for td in tds])
    if problems:  # refused before any state is derived or an evaluation is stored (STA §16, STA-REQ-006/022)
        raise AssuranceConfigurationError("CONTROL_TEST_POLICY_INVALID", "; ".join(problems),
                                          control_implementation_id=ci.control_implementation_id,
                                          policy_version_id=policy.policy_version_id)
    test_rows = []
    deps = {"control_implementation": ci.control_implementation_id, "policy": policy.policy_version_id, "runs": [], "evidence": []}
    for td in tds:
        runs = db.execute(select(TestRun).where(TestRun.tenant_id == ci.tenant_id,
                                                TestRun.test_definition_id == td.test_definition_id,
                                                TestRun.record_seq <= cutoff_seq)
                          .order_by(TestRun.record_seq, TestRun.test_run_id)).scalars().all()
        runs = [r for r in runs if _overlap(r.window_from, r.window_to, a, b)]
        deps["runs"] += [r.test_run_id for r in runs]
        status, reasons, ev_rows, covered = None, [], [], []
        fails = [r for r in runs if r.result == "FAIL"]
        if fails:
            status = "FAIL"
            reasons.append("TEST_FAIL")
        else:
            conflict = False
            for r in runs:
                if r.result == "PASS":
                    st, rs, rows = _evidence_admissible(db, r, td, policy, a, b, cutoff_seq)
                    ev_rows += rows
                    deps["evidence"] += [x["evidence_id"] for x in rows]
                    if st == "OK":
                        covered.append((ensure_utc(r.window_from), ensure_utc(r.window_to)))
                    elif st == "CONFLICT":
                        conflict = True
                        reasons += rs
                    else:
                        reasons += rs
                elif r.result in ("ERROR", "INCONCLUSIVE", "SKIPPED"):
                    reasons.append({"ERROR": "TEST_ERROR", "INCONCLUSIVE": "TEST_INCONCLUSIVE", "SKIPPED": "TEST_NOT_RUN"}[r.result])
            if conflict:
                status = "CONFLICT"
            elif _covers(covered, a, b):
                status = "OK"
                reasons = []
            else:
                status = "UNKNOWN"
                if not runs:
                    reasons.append("TEST_NOT_RUN")
                elif covered:
                    reasons.append("COVERAGE_GAP")
                if not reasons:
                    reasons.append("COVERAGE_GAP")
        test_rows.append({"test_definition_id": td.test_definition_id, "name": td.name, "required": td.required,
                          "primary": td.primary, "ipe_required": td.ipe_required, "status": status,
                          "reason_codes": sorted(set(reasons)), "runs": [{"test_run_id": r.test_run_id, "result": r.result,
                                                                         "source_system": r.source_system,
                                                                         "external_status": r.external_status,
                                                                         "window": [r.window_from, r.window_to]} for r in runs],
                          "evidence": ev_rows, "degrades_on_fail": td.degrades_on_fail})
    state, rule, reasons = _aggregate(policy, test_rows)
    out = {"state": state, "reason_codes": reasons, "tests": test_rows, "precedence_rule": rule,
           "policy_version_id": policy.policy_version_id, "aggregation_mode": policy.aggregation_mode,
           "automation_requirement": policy.automation_requirement}
    dep_digest = digest({**deps, "window": [a, b], "cutoff": cutoff_seq, "v": codes.CONTROL_EVALUATOR_VERSION})
    out["dependency_digest"] = dep_digest
    if persist:
        # Concurrent requests can store twins (nothing makes the digest unique); always reuse the earliest.
        existing = db.execute(select(ControlEvaluation).where(ControlEvaluation.tenant_id == ci.tenant_id,
                                                              ControlEvaluation.dependency_digest == dep_digest)
                              .order_by(ControlEvaluation.created_at, ControlEvaluation.evaluation_id)).scalars().first()
        if existing:
            out["evaluation_id"] = existing.evaluation_id
        else:
            ce = ControlEvaluation(tenant_id=ci.tenant_id, control_implementation_id=ci.control_implementation_id,
                                   policy_version_id=policy.policy_version_id, window_from=a, window_to=b,
                                   record_cutoff_seq=cutoff_seq, state=state, reason_codes=reasons,
                                   inputs=_jsonable({"tests": test_rows, "deps": deps}), precedence_rule=rule,
                                   evaluator_version=codes.CONTROL_EVALUATOR_VERSION, dependency_digest=dep_digest)
            db.add(ce)
            db.flush()
            out["evaluation_id"] = ce.evaluation_id
            # STA §17: a newly stored FAILING / REQUIRES_REVIEW evaluation creates or links its control-level issue in
            # the same transaction (committed together, rolled back together). A reused evaluation (same digest) is
            # not new, so retries raise nothing; previews (persist=False) store nothing and raise nothing.
            from . import governance
            governance.raise_control_issue(db, ci=ci, evaluation=ce)
    return out


def _jsonable(o):
    import json
    from ..platform import canonical_json
    return json.loads(canonical_json(o))


def _covers(intervals, a, b) -> bool:
    cur = a
    for f, t in sorted(intervals):
        if f > cur:
            return False
        cur = max(cur, t)
        if cur >= b:
            return True
    return cur >= b


def _aggregate(policy: ControlTestPolicy, tests: list[dict]) -> tuple[str, str, list[str]]:
    reasons = sorted({r for t in tests for r in t["reason_codes"]})
    if not tests:
        return "NOT_EVALUATED", "3:NOT_EVALUATED", ["TEST_NOT_RUN"]
    req = [t for t in tests if t["required"]]
    opt = [t for t in tests if not t["required"]]
    degraded = any(t["status"] == "FAIL" and t["degrades_on_fail"] for t in opt)
    mode = policy.aggregation_mode
    # Defence in depth: evaluate_control validates the policy first; an unknown mode or out-of-range k never falls
    # through to another mode or to a vacuous threshold.
    if mode not in AGGREGATION_MODES:
        raise AssuranceConfigurationError("CONTROL_TEST_POLICY_INVALID", f"unsupported aggregation_mode {mode!r}")
    if not req:
        raise AssuranceConfigurationError("CONTROL_TEST_POLICY_INVALID", "no required test in the policy")
    if mode == "K_OF_N_TESTS":
        k = policy.k_threshold
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(req):
            raise AssuranceConfigurationError("CONTROL_TEST_POLICY_INVALID",
                                              f"k_threshold {k!r} outside 1..{len(req)} required tests")
        ok = sum(t["status"] == "OK" for t in req)
        conflict = sum(t["status"] == "CONFLICT" for t in req)
        unknown = sum(t["status"] == "UNKNOWN" for t in req)
        if ok >= k:
            return ("DEGRADED", "4:DEGRADED", reasons + ["DEGRADATION_RULE"]) if degraded else ("OPERATING", "5:OPERATING", [])
        if ok + conflict + unknown < k:
            return "FAILING", "1:FAILING", reasons
        if conflict:
            return "REQUIRES_REVIEW", "2:REQUIRES_REVIEW", reasons
        return "NOT_EVALUATED", "3:NOT_EVALUATED", reasons
    if mode == "PRIMARY_PLUS_CORROBORATION":
        prim = [t for t in req if t["primary"]]
        if len(prim) != 1:  # "Named primary test": never an implicit choice of the first required test
            raise AssuranceConfigurationError("CONTROL_TEST_POLICY_INVALID",
                                              "PRIMARY_PLUS_CORROBORATION needs exactly one named primary required test")
        if any(t["status"] == "FAIL" for t in prim):
            return "FAILING", "1:FAILING", reasons
        req = prim + [t for t in req if t not in prim]
    # ALL_REQUIRED (and corroboration remainder)
    if any(t["status"] == "FAIL" for t in req):
        return "FAILING", "1:FAILING", reasons
    if any(t["status"] == "CONFLICT" for t in req):
        return "REQUIRES_REVIEW", "2:REQUIRES_REVIEW", reasons
    if any(t["status"] in ("UNKNOWN", None) for t in req):
        return "NOT_EVALUATED", "3:NOT_EVALUATED", reasons
    if degraded:
        return "DEGRADED", "4:DEGRADED", sorted(set(reasons + ["DEGRADATION_RULE"]))
    return "OPERATING", "5:OPERATING", []


# ------------------------------------------------------------------ expression evaluation (pure four-valued)
def combine_all(vals):
    if UNSAT in vals: return UNSAT
    if REV in vals: return REV
    if UNK in vals: return UNK
    return SAT


def combine_any(vals):
    if SAT in vals: return SAT
    if REV in vals: return REV
    if UNK in vals: return UNK
    return UNSAT


def combine_k(vals, k):
    s, r, u = vals.count(SAT), vals.count(REV), vals.count(UNK)
    if s >= k: return SAT
    if s + r + u < k: return UNSAT
    if r > 0: return REV
    return UNK


def eval_expression(node: dict, leaf_fn, comp_fn, occ_fn) -> dict:
    nt = node["node_type"]
    base = {"node_id": node.get("node_id"), "node_type": nt}
    if nt == "CONTROL":
        # An exact mapping (STA §07 "exact ControlImplementation") is passed through; a selector leaf keeps the
        # one-argument call, so selector traces and digests are unchanged.
        impl = node.get("control_implementation_id")
        v, info = leaf_fn(node["control_key"], impl) if impl is not None else leaf_fn(node["control_key"])
        base.update(result=v, control_key=node["control_key"], **info)
        return base
    if nt == "OCCURRENCES":
        v, info = occ_fn()
        base.update(result=v, **info)
        return base
    if nt == "OPTIONAL_EVIDENCE":
        base.update(result="NOT_BINDING", note=node.get("note"))
        return base
    if nt == "COMPENSATION":
        p = eval_expression(node["primary"], leaf_fn, comp_fn, occ_fn)
        valid, cinfo = comp_fn(node.get("compensation_id"))
        if p["result"] == SAT:
            c = {"node_id": node["compensating"].get("node_id"), "node_type": node["compensating"]["node_type"],
                 "result": "NOT_EVALUATED", "short_circuit_reason": "Primary satisfied; compensation not needed."}
            res = SAT
        else:
            c = eval_expression(node["compensating"], leaf_fn, comp_fn, occ_fn)
            if valid:
                res = combine_any([p["result"], c["result"]])
            elif cinfo.get("reason") == "COMPENSATION_UNRESOLVABLE":
                c["compensation_invalid"] = cinfo
                res = combine_any([p["result"], REV])  # cannot reconstruct the approved branch -> governed review
            else:
                # expired / out-of-scope / unapproved: the alternative path is absent; the primary result stands
                c["compensation_invalid"] = cinfo
                res = p["result"]
            if res == SAT:
                base["satisfied_via_compensation"] = True
        base.update(result=res, children=[p, c], compensation=cinfo)
        return base
    if nt not in ("ALL", "ANY", "K_OF_N"):
        raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_INVALID", f"unsupported node type {nt!r}",
                                          node_id=node.get("node_id"))
    kids = node.get("children")
    if not isinstance(kids, list):
        raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_INVALID", f"{nt} node has no children list",
                                          node_id=node.get("node_id"))
    traces = [eval_expression(k, leaf_fn, comp_fn, occ_fn) for k in kids]
    vals = [t["result"] for t in traces if t["result"] != "NOT_BINDING"]
    if not vals:  # an empty ALL/ANY/K_OF_N proves nothing; never vacuous truth (STA §09, §16)
        raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_INVALID", f"{nt} node has no binding children",
                                          node_id=node.get("node_id"))
    if nt == "ALL":
        res = combine_all(vals)
    elif nt == "ANY":
        res = combine_any(vals)
    else:
        k = node.get("k")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(vals):
            raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_INVALID",
                                              f"K_OF_N k {k!r} outside 1..{len(vals)} binding children",
                                              node_id=node.get("node_id"))
        res = combine_k(vals, k)
        base["k"] = k
    base.update(result=res, children=traces)
    return base


# ------------------------------------------------------------------ period roll-up
def _resolve_impl(db, tenant_id, control_key, subject: Subject, a, b, cutoff):
    cd = db.execute(select(ControlDefinition).where(ControlDefinition.key == control_key)).scalars().first()
    if not cd:
        return None
    subj_ids = [subject.subject_id]
    if subject.entity_id:
        ent = db.execute(select(Subject).where(Subject.tenant_id == tenant_id, Subject.entity_id == subject.entity_id,
                                               Subject.subject_type == "LEGAL_ENTITY")
                         .order_by(Subject.subject_id)).scalars().first()
        if ent:
            subj_ids.append(ent.subject_id)
    impls = db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == tenant_id,
                                                           ControlImplementation.control_definition_id == cd.control_definition_id,
                                                           ControlImplementation.subject_id.in_(subj_ids),
                                                           ControlImplementation.record_seq <= cutoff)
                       .order_by(ControlImplementation.record_seq, ControlImplementation.control_implementation_id)).scalars().all()
    impls.sort(key=lambda i: subj_ids.index(i.subject_id))
    for i in impls:
        if _overlap(i.valid_from, i.valid_to, a, b):
            return i
    return impls[0] if impls else None


def _control_keys(node) -> set[str]:
    out = set()
    if node.get("node_type") == "CONTROL":
        out.add(node["control_key"])
    for k in ("children",):
        for c in node.get(k, []) or []:
            out |= _control_keys(c)
    for k in ("primary", "compensating"):
        if node.get(k):
            out |= _control_keys(node[k])
    return out


def _comp_ids(node) -> set[str]:
    out = set()
    if node.get("node_type") == "COMPENSATION" and node.get("compensation_id"):
        out.add(node["compensation_id"])
    for c in node.get("children", []) or []:
        out |= _comp_ids(c)
    for k in ("primary", "compensating"):
        if node.get(k):
            out |= _comp_ids(node[k])
    return out


def compute_snapshot(db: Session, *, tenant_id: str, obligation_lineage_id: str, period_from: datetime,
                     period_to: datetime, record_cutoff_seq: int | None, perspective: str, actor_id: str,
                     persist: bool = True, evaluated_at: datetime | None = None) -> dict:
    """``evaluated_at``: the instant that decides whether an unfulfilled occurrence's due time has passed (MISSED) or
    not yet (PENDING). A reproduction passes the stored snapshot's ``created_at``, which a persisted snapshot sets to
    exactly this instant, so rebuilding it later gives the same result. Without it the request instant is used
    (``evaluation_instant_source`` REQUEST_TIME; a persisted snapshot stores it as created_at). No other wall-clock time
    is read."""
    from ..platform import current_watermark
    now = ensure_utc(evaluated_at) if evaluated_at is not None else datetime.now(timezone.utc)
    period_from, period_to = ensure_utc(period_from), ensure_utc(period_to)
    if period_to <= period_from:
        raise HTTPException(422, "Assurance period must be a non-empty half-open interval")
    cutoff = record_cutoff_seq if record_cutoff_seq is not None else current_watermark(db)["record_seq"]
    revs = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant_id,
                                                       ObligationInstance.lineage_id == obligation_lineage_id,
                                                       ObligationInstance.record_from_seq <= cutoff,
                                                       or_(ObligationInstance.record_to_seq.is_(None),
                                                           ObligationInstance.record_to_seq > cutoff))
                       .order_by(ObligationInstance.record_from_seq, ObligationInstance.revision_id)).scalars().all()
    if not revs:
        raise HTTPException(404, "Obligation not known at this record cutoff")
    obl = revs[-1]  # the most recently recorded revision known at the cutoff
    subject = db.get(Subject, obl.subject_id)
    odef = db.get(ObligationDefinition, obl.obligation_definition_id)
    exprs = db.execute(select(SatisfactionExpressionVersion).where(
        SatisfactionExpressionVersion.tenant_id == tenant_id,
        SatisfactionExpressionVersion.obligation_lineage_id == obligation_lineage_id,
        SatisfactionExpressionVersion.record_seq <= cutoff)
        .order_by(SatisfactionExpressionVersion.record_seq, SatisfactionExpressionVersion.expression_version_id)).scalars().all()
    if not exprs:
        raise HTTPException(409, "No SatisfactionExpressionVersion for obligation (configuration error)")
    checked: set[str] = set()

    def in_force(a, b):
        """The SatisfactionExpressionVersion in force for [a, b): the highest version whose valid period covers the
        segment, among versions recorded by the cutoff. No fallback to a version that was not in force (audit F-09;
        STA-REQ-021, TIM-REQ-015): an applicable segment without one is a configuration error, not a state. The chosen
        version is validated once (STA §16)."""
        e = next((e for e in sorted(exprs, key=lambda e: -e.version)
                  if ensure_utc(e.valid_from) <= a and (e.valid_to is None or ensure_utc(e.valid_to) >= b)), None)
        if e is None:
            raise AssuranceConfigurationError(
                "NO_EXPRESSION_IN_FORCE", "No SatisfactionExpressionVersion is in force for an applicable segment",
                obligation_lineage_id=obligation_lineage_id, segment=[a.isoformat(), b.isoformat()],
                record_cutoff_seq=cutoff)
        if e.expression_version_id not in checked:
            from .satisfaction import hash_intact
            if not hash_intact(e):  # substituted in place after release: never evaluated as the released version
                raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_HASH_MISMATCH",
                                                  "the stored expression no longer matches its recorded hash",
                                                  expression_version_id=e.expression_version_id, version=e.version)
            problems = expression_problems(e.ast)
            if problems:
                raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_INVALID", "; ".join(problems[:5]),
                                                  expression_version_id=e.expression_version_id, version=e.version)
            checked.add(e.expression_version_id)
        return e
    active_ranges = [(ensure_utc(r.valid_from), ensure_utc(r.valid_to)) for r in revs if r.lifecycle == "ADOPTED"]

    # ---- boundary collection (ZA-TIM-001 §9)
    bounds = {period_from, period_to}
    def add(*dts):
        for d in dts:
            d = ensure_utc(d)
            if d and period_from < d < period_to:
                bounds.add(d)
    for r in revs:
        add(r.valid_from, r.valid_to)
    for e in exprs:
        add(e.valid_from, e.valid_to)
    impl_ids, keys_all = set(), set()
    for e in exprs:
        keys_all |= _control_keys(e.ast)
    for key in keys_all:
        cd = db.execute(select(ControlDefinition).where(ControlDefinition.key == key)).scalars().first()
        if not cd:
            continue
        for ci in db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == tenant_id,
                                                                 ControlImplementation.control_definition_id == cd.control_definition_id)).scalars():
            impl_ids.add(ci.control_implementation_id)
            add(ci.valid_from, ci.valid_to)
            for pol in db.execute(select(ControlTestPolicy).where(ControlTestPolicy.tenant_id == tenant_id,
                                                                  ControlTestPolicy.control_implementation_id == ci.control_implementation_id)).scalars():
                for td in db.execute(select(TestDefinition).where(TestDefinition.tenant_id == tenant_id,
                                                                  TestDefinition.policy_version_id == pol.policy_version_id)).scalars():
                    for run in db.execute(select(TestRun).where(TestRun.tenant_id == tenant_id,
                                                                TestRun.test_definition_id == td.test_definition_id,
                                                                TestRun.record_seq <= cutoff)).scalars():
                        add(run.window_from, run.window_to)
                        for eid in run.evidence_ids:
                            art = db.get(EvidenceArtifact, eid)
                            if art and art.tenant_id == tenant_id:
                                add(art.valid_until)
    comps = db.execute(select(Compensation).where(Compensation.tenant_id == tenant_id,
                                                  Compensation.obligation_lineage_id == obligation_lineage_id,
                                                  Compensation.record_seq <= cutoff)
                       .order_by(Compensation.record_seq, Compensation.compensation_id)).scalars().all()
    for c in comps:
        add(c.valid_from, c.valid_to)
    comp_status = _compensation_status_at(db, tenant_id, comps, cutoff)
    exceptions = db.execute(select(ExceptionRecord).where(ExceptionRecord.tenant_id == tenant_id,
                                                          ExceptionRecord.record_seq <= cutoff,
                                                          or_(ExceptionRecord.target_id == obligation_lineage_id,
                                                              ExceptionRecord.target_id.in_(list(impl_ids) or ["-"])))
                            .order_by(ExceptionRecord.record_seq, ExceptionRecord.exception_id)).scalars().all()
    for x in exceptions:
        add(x.valid_from, x.valid_to)
    exc_hist = exception_states_at(db, tenant_id, exceptions, cutoff)
    occs = db.execute(select(ObligationOccurrence).where(ObligationOccurrence.tenant_id == tenant_id,
                                                         ObligationOccurrence.obligation_lineage_id == obligation_lineage_id,
                                                         ObligationOccurrence.record_seq <= cutoff)
                      .order_by(ObligationOccurrence.record_seq, ObligationOccurrence.occurrence_id)).scalars().all()
    for o in occs:
        add(o.due_at)
    occ_hist = occurrence_states_at(db, tenant_id, occs, cutoff)
    pts = sorted(bounds)
    raw = list(zip(pts[:-1], pts[1:]))

    deps = {"obligation_revisions": sorted(r.revision_id for r in revs), "expression_versions": sorted(e.expression_version_id for e in exprs),
            "control_implementations": set(), "control_evaluation_digests": set(), "test_runs": set(), "evidence": set(),
            "compensations": sorted(c.compensation_id for c in comps), "exceptions": sorted(x.exception_id for x in exceptions),
            "occurrences": sorted(o.occurrence_id for o in occs)}
    segments, excluded = [], []
    for a, b in raw:
        if not any(f <= a and (t is None or t >= b) for f, t in active_ranges):
            excluded.append({"segment_range": [a, b], "reason": "OBLIGATION_NOT_APPLICABLE"})
            continue
        expr = in_force(a, b)
        leaves = []

        def leaf_fn(key, impl_id=None, a=a, b=b, leaves=leaves):
            exact = {}
            if impl_id is not None:  # exact control mapping (engines/satisfaction.py): never re-resolved by selector
                from .satisfaction import binding_problem
                problem = binding_problem(db, tenant_id, subject, key, impl_id) if isinstance(impl_id, str) else \
                    "control_implementation_id must be a string"
                if problem:  # stored mapping no longer valid: a configuration error, never a leaf state (STA §16)
                    raise AssuranceConfigurationError("CONTROL_MAPPING_INVALID", problem, control_key=key,
                                                      expression_version_id=expr.expression_version_id)
                exact = {"binding": "EXACT"}
                ci = db.get(ControlImplementation, impl_id)
                if ci.record_seq > cutoff:  # not yet known at this record cutoff
                    ci = None
            else:
                ci = _resolve_impl(db, tenant_id, key, subject, a, b, cutoff)
            if ci is None:
                info = {"control_state": "NOT_EVALUATED", "reason_codes": ["CONTROL_NOT_IMPLEMENTED"], **exact}
                leaves.append({"control_key": key, **info})
                return UNK, info
            ce = evaluate_control(db, ci, a, b, cutoff, persist=persist)
            deps["control_implementations"].add(ci.control_implementation_id)
            deps["control_evaluation_digests"].add(ce["dependency_digest"])
            for t in ce["tests"]:
                deps["test_runs"] |= {r["test_run_id"] for r in t["runs"]}
                deps["evidence"] |= {e["evidence_id"] for e in t["evidence"]}
            info = {"control_implementation_id": ci.control_implementation_id, "control_state": ce["state"],
                    "reason_codes": ce["reason_codes"], "control_evaluation_id": ce.get("evaluation_id"),
                    "precedence_rule": ce["precedence_rule"], **exact}
            leaves.append({"control_key": key, **info})
            return LEAF_MAP[ce["state"]], info

        def comp_fn(cid, a=a, b=b):
            c = next((c for c in comps if c.compensation_id == cid), None)
            if c is None:
                return False, {"compensation_id": cid, "valid": False, "reason": "COMPENSATION_UNRESOLVABLE"}
            status, source = comp_status[cid]
            ok = status == "APPROVED" and ensure_utc(c.valid_from) <= a and (c.valid_to is None or ensure_utc(c.valid_to) >= b)
            return ok, {"compensation_id": cid, "valid": ok, "status": status, "status_source": source,
                        "reason": None if ok else "COMPENSATION_EXPIRED", "rationale": c.equivalence_rationale}

        def occ_fn(a=a, b=b):
            due = [o for o in occs if ensure_utc(o.due_at) <= b and ensure_utc(o.due_at) > period_from and ensure_utc(o.window_from) < b]
            if not due:
                return SAT, {"occurrences": [], "note": "No occurrence due in segment"}
            vals, info = [], []
            for o in due:
                h = occ_hist[o.occurrence_id]  # as known at the cutoff, never today's projection
                fulfilled_at = h["fulfilled_at"]
                on_time = fulfilled_at is not None and ensure_utc(fulfilled_at) <= ensure_utc(o.due_at)
                if h["problem"]:
                    # Unresolved history prevents a deterministic result: REQUIRES_REVIEW (ZA-TIM-001 §9), never a
                    # substitute read of the current row.
                    v, r = REV, h["problem"]
                elif h["disputed"]:
                    v, r = REV, "SOURCE_CONFLICT"
                elif on_time and h["evidence_ids"]:
                    v, r = SAT, None
                elif ensure_utc(o.due_at) <= min(b, now) and not on_time:
                    v, r = UNSAT, "OCCURRENCE_MISSED"
                else:
                    v, r = UNK, "OCCURRENCE_PENDING"
                vals.append(v)
                info.append({"occurrence_id": o.occurrence_id, "due_at": o.due_at, "result": v, "reason": r})
            return combine_all(vals), {"occurrences": info}

        trace = eval_expression(expr.ast, leaf_fn, comp_fn, occ_fn)
        state = SEGMENT_STATE[trace["result"]]
        treat = [{"exception_id": x.exception_id, "state": "APPROVED", "target_type": x.target_type, "valid": [x.valid_from, x.valid_to]}
                 for x in exceptions
                 if exc_hist[x.exception_id]["state"] == "APPROVED" and _overlap(x.valid_from, x.valid_to, a, b)]
        reasons = sorted({r for l in leaves for r in l.get("reason_codes", [])})
        segments.append({"segment_range": [a, b], "expression_version_id": expr.expression_version_id,
                         "expression_result": trace["result"], "assurance_state": state, "leaf_evaluations": leaves,
                         "expression_trace": trace, "exception_treatments": treat, "reason_codes": reasons,
                         "dependency_digest": digest({"leaves": [{k: v for k, v in l.items() if k != "control_evaluation_id"} for l in leaves], "expr": expr.expression_version_id, "range": [a, b]})})
    if not segments:
        raise HTTPException(409, {"error": "NO_ACTIVE_ASSURANCE", "message": "Obligation not applicable for the entire requested period",
                                  "excluded": _jsonable(excluded)})
    states = {s["assurance_state"] for s in segments}
    period_state = next(s for s in PERIOD_ORDER if s in states)
    coalesced = _coalesce(segments)
    deps_json = _jsonable({k: sorted(v) if isinstance(v, set) else v for k, v in deps.items()})
    dep_digest = digest(deps_json)
    period_reasons = sorted({r for s in segments for r in s["reason_codes"]})
    treatments = []
    for x in exceptions:
        if _overlap(x.valid_from, x.valid_to, period_from, period_to):
            h = exc_hist[x.exception_id]
            t = {"exception_id": x.exception_id, "state": h["state"], "valid": [x.valid_from, x.valid_to],
                 "note": f"{period_state} + {h['state'] or 'UNRESOLVED'} EXCEPTION — underlying state unchanged"}
            if h["problem"]:
                t["history_problem"] = h["problem"]
            treatments.append(t)
    # Where each exception's and occurrence's historical state came from (TIM-REQ-015), and every gap in that history.
    historical_state = {
        "exceptions": [{"exception_id": k, **{f: v[f] for f in ("state", "source", "problem", "event_ids")}}
                       for k, v in exc_hist.items()],
        "occurrences": [{"occurrence_id": k, **{f: v[f] for f in ("fulfilled_at", "evidence_ids", "disputed", "source",
                                                                   "problem", "event_ids")}}
                        for k, v in occ_hist.items()]}
    history_problems = sorted({v["problem"] for v in (*exc_hist.values(), *occ_hist.values()) if v["problem"]})
    period_reasons = sorted(set(period_reasons) | set(history_problems))
    result = {"tenant_id": tenant_id, "entity_id": obl.entity_id, "obligation_lineage_id": obligation_lineage_id,
              "obligation_revision_id": obl.revision_id, "obligation_definition": odef.key,
              "subject": {"subject_id": subject.subject_id, "display_name": subject.display_name, "type": subject.subject_type},
              "period": [period_from, period_to], "record_cutoff_seq": cutoff, "perspective": perspective, "state": period_state,
              "segment_count": len(segments), "segments": _jsonable(coalesced), "excluded_segments": _jsonable(excluded),
              "dependency_manifest": deps_json, "dependency_digest": dep_digest, "reason_codes": period_reasons,
              "exception_treatments": _jsonable(treatments), "rollup_engine_version": codes.ROLLUP_ENGINE_VERSION,
              "precedence_explanation": _explain(period_state, segments),
              "legal_date_policy": "Assurance periods are UTC instants; legal dates convert at UTC start-of-day (explicit policy).",
              "evaluation_instant_source": "SNAPSHOT_CREATED_AT" if evaluated_at is not None else "REQUEST_TIME",
              "historical_state": _jsonable(historical_state), "history_problems": history_problems}
    if evaluated_at is not None:
        # Echoed only when the caller fixed it (a reproduction): identical inputs keep byte-identical output
        # (tests/test_snapshot_determinism.py). A persisted snapshot returns the same instant as its created_at.
        result["evaluation_instant"] = now
    if not persist:
        return result
    seq = next_record_seq(db)
    snap = AssuranceSnapshot(tenant_id=tenant_id, entity_id=obl.entity_id, obligation_lineage_id=obligation_lineage_id,
                             obligation_revision_id=obl.revision_id, period_from=period_from, period_to=period_to,
                             record_cutoff_seq=cutoff, perspective=perspective, expression_version_id=segments[-1]["expression_version_id"],
                             rollup_engine_version=codes.ROLLUP_ENGINE_VERSION, state=period_state,
                             segments=result["segments"], dependency_manifest=deps_json, dependency_digest=dep_digest,
                             snapshot_hash=digest({"state": period_state, "segments": result["segments"], "deps": dep_digest,
                                                   "period": [period_from, period_to], "cutoff": cutoff}),
                             exception_treatments=result["exception_treatments"], reason_codes=period_reasons, created_by=actor_id,
                             created_at=now)  # the evaluation instant, stored so /reproduce rebuilds with the same one
    db.add(snap)
    db.flush()
    prior = db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == tenant_id,
                                                       AssuranceSnapshot.obligation_lineage_id == obligation_lineage_id,
                                                       AssuranceSnapshot.snapshot_id != snap.snapshot_id)
                       .order_by(AssuranceSnapshot.created_at.desc(), AssuranceSnapshot.snapshot_id.desc())).scalars().first()
    emit(db, event_type="assurance.snapshot_created", aggregate_type="AssuranceSnapshot", aggregate_id=snap.snapshot_id,
         tenant_id=tenant_id, entity_id=obl.entity_id, record_seq=seq,
         payload={"obligation_lineage_id": obligation_lineage_id, "state": period_state, "period": [period_from, period_to],
                  "dependency_digest": dep_digest})
    if prior and prior.state != period_state:
        emit(db, event_type="assurance.state_changed", aggregate_type="AssuranceSnapshot", aggregate_id=snap.snapshot_id,
             tenant_id=tenant_id, payload={"prior_snapshot": prior.snapshot_id, "old": prior.state, "new": period_state})
    _issue_hooks(db, tenant_id, snap, odef)
    result["snapshot_id"] = snap.snapshot_id
    result["snapshot_hash"] = snap.snapshot_hash
    result["created_at"] = snap.created_at
    return result


def _compensation_status_at(db, tenant_id: str, comps, cutoff: int) -> dict:
    """Each compensation's status as known at the record cutoff (audit F-08; STA-REQ-021, TIM-REQ-006).

    The ``assurance_compensation_revision`` row is a projection whose ``status`` is updated in place (migration 0010
    allows only ``status`` and ``approved_by``); its append-only history is ``assurance_compensation_event`` (0008).
    A historical snapshot must use the status as of its cutoff, not today's: otherwise a later revocation would change
    the result of an earlier record perspective. A compensation with no recorded history at all (created before 0008,
    e.g. seeded rows) falls back to its projection status, labelled as such in the trace."""
    from ..models import CompensationEvent
    ids = [c.compensation_id for c in comps]
    events = db.execute(select(CompensationEvent).where(CompensationEvent.tenant_id == tenant_id,
                                                        CompensationEvent.compensation_id.in_(ids or ["-"]))
                        .order_by(CompensationEvent.record_seq, CompensationEvent.event_id)).scalars().all()
    out = {}
    for c in comps:
        own = [e for e in events if e.compensation_id == c.compensation_id]
        known = [e for e in own if e.record_seq <= cutoff]
        if known:
            out[c.compensation_id] = (known[-1].to_status, "EVENT_HISTORY")
        elif own:  # history exists but none of it was known at the cutoff: nothing was approved then
            out[c.compensation_id] = ("PENDING_APPROVAL", "EVENT_HISTORY")
        else:
            out[c.compensation_id] = (c.status, "PROJECTION_NO_HISTORY")
    return out


# Historical exception and occurrence state (audit F2; STA-REQ-021, TIM-REQ-006/015). The rows assurance_exception_revision
# and assurance_obligation_occurrence are current-state projections updated in place (migration 0010 allows only their
# lifecycle columns to change); what was true at a record cutoff is in their append-only event tables (0008). A snapshot
# reads the events recorded by its cutoff, never today's projection. Where that history is missing or does not prove
# itself, the gap is reported (problem code) and the projection is NOT substituted.
EXCEPTION_TRANSITIONS = {("PENDING_APPROVAL", "APPROVED"), ("PENDING_APPROVAL", "REJECTED"), ("APPROVED", "REVOKED")}
BASELINE_SOURCE = "BASELINE_0008"  # migration 0008 backfill: the state at migration time, stamped at the row's record_seq


def _same_instant(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and ensure_utc(a) == ensure_utc(b))


def _ordered_unambiguously(events) -> bool:
    """One object's events must have distinct record sequences: within one sequence their order is not knowable."""
    seqs = [e.record_seq for e in events]
    return len(seqs) == len(set(seqs))


def exception_states_at(db: Session, tenant_id: str, exceptions, cutoff: int) -> dict[str, dict]:
    """Each exception's lifecycle state as known at ``cutoff``, from assurance_exception_event only.

    {exception_id: {"state", "source", "problem", "event_ids"}}: ``state`` is the to_state of the last event recorded by
    the cutoff; ``source`` is EVENT_HISTORY, or BASELINE_0008 when that event is the migration 0008 baseline (state at
    migration time, not necessarily at the row's own record sequence). ``state`` is None, with a ``problem``, when the
    history is missing (EXCEPTION_HISTORY_MISSING) or does not prove itself (EXCEPTION_HISTORY_INCONSISTENT: no event by
    the cutoff although the row was recorded by then, an ambiguous order, a broken from_state chain, a transition the
    lifecycle does not allow, identity fields that differ from the immutable row, or a projection that disagrees with a
    fully known history)."""
    from .governance import MIGRATION_BASELINE_EVENT as _BASELINE
    ids = [x.exception_id for x in exceptions]
    from ..models import ExceptionEvent
    events = db.execute(select(ExceptionEvent).where(ExceptionEvent.tenant_id == tenant_id,
                                                     ExceptionEvent.exception_id.in_(ids or ["-"]))
                        .order_by(ExceptionEvent.record_seq, ExceptionEvent.event_id)).scalars().all()
    out = {}
    for x in exceptions:
        own = [e for e in events if e.exception_id == x.exception_id]
        known = [e for e in own if e.record_seq <= cutoff]
        res = {"state": None, "source": None, "problem": None, "event_ids": [e.event_id for e in known]}
        out[x.exception_id] = res
        if not own:
            res["problem"] = "EXCEPTION_HISTORY_MISSING"
            continue
        ok = bool(known) and _ordered_unambiguously(own)
        if ok:
            first = known[0]
            ok = first.event_type in ("REQUESTED", _BASELINE) and first.from_state is None and \
                (first.event_type == _BASELINE or first.to_state == "PENDING_APPROVAL")
        for prev, e in zip(known, known[1:]):
            ok = ok and e.event_type != _BASELINE and e.from_state == prev.to_state and \
                (e.from_state, e.to_state) in EXCEPTION_TRANSITIONS and e.event_type == e.to_state
        ok = ok and all(e.target_type == x.target_type and e.target_id == x.target_id
                        and _same_instant(e.valid_from, x.valid_from) and _same_instant(e.valid_to, x.valid_to)
                        for e in known)
        if ok and len(known) == len(own) and known[-1].to_state != x.state:
            ok = False  # the whole history is known, yet the projection says otherwise
        if not ok:
            res["problem"] = "EXCEPTION_HISTORY_INCONSISTENT"
            continue
        res["state"] = known[-1].to_state
        res["source"] = BASELINE_SOURCE if known[-1].event_type == _BASELINE else "EVENT_HISTORY"
    return out


def occurrence_states_at(db: Session, tenant_id: str, occs, cutoff: int) -> dict[str, dict]:
    """Each occurrence's fulfilment and dispute as known at ``cutoff``, from assurance_obligation_occurrence_event.

    {occurrence_id: {"fulfilled_at", "evidence_ids", "disputed", "source", "problem", "event_ids"}}.
      * fulfilment and its evidence references come from the FULFILLED event recorded by the cutoff (or the 0008
        BASELINE event, labelled BASELINE_0008), never from the projection;
      * ``disputed``: the row's ``disputed`` column, immutable since insertion (0010 immutable_columns), or a DISPUTED
        event recorded by the cutoff;
      * no events at all: an occurrence that is still unfulfilled is reconstructed exactly (fulfil_occurrence always
        writes its FULFILLED event and the projection can only go from unfulfilled to fulfilled), source NO_EVENTS; a
        fulfilled one has no recorded fulfilment time, so OCCURRENCE_HISTORY_MISSING;
      * OCCURRENCE_HISTORY_INCONSISTENT: no event by the cutoff although the row was recorded by then, an ambiguous order,
        a first event other than SCHEDULED/BASELINE, more than one fulfilment, a fulfilment event without a fulfilment
        time, schedule fields that differ from the immutable row, or a projection that disagrees with a fully known
        history."""
    from .governance import MIGRATION_BASELINE_EVENT as _BASELINE
    ids = [o.occurrence_id for o in occs]
    from ..models import ObligationOccurrenceEvent as OccEvent
    events = db.execute(select(OccEvent).where(OccEvent.tenant_id == tenant_id, OccEvent.occurrence_id.in_(ids or ["-"]))
                        .order_by(OccEvent.record_seq, OccEvent.event_id)).scalars().all()
    out = {}
    for o in occs:
        own = [e for e in events if e.occurrence_id == o.occurrence_id]
        known = [e for e in own if e.record_seq <= cutoff]
        res = {"fulfilled_at": None, "evidence_ids": [], "disputed": bool(o.disputed), "source": None, "problem": None,
               "event_ids": [e.event_id for e in known]}
        out[o.occurrence_id] = res
        if not own:
            if o.fulfilled_at is None and not o.evidence_ids:
                res["source"] = "NO_EVENTS"
            else:
                res["problem"] = "OCCURRENCE_HISTORY_MISSING"
            continue
        fulfils = [e for e in own if e.event_type == "FULFILLED" or (e.event_type == _BASELINE and e.fulfilled_at)]
        ok = bool(known) and _ordered_unambiguously(own) and known[0].event_type in ("SCHEDULED", _BASELINE) \
            and len(fulfils) <= 1 and all(e.event_type != _BASELINE for e in known[1:]) \
            and all(e.fulfilled_at is not None for e in own if e.event_type == "FULFILLED") \
            and all(_same_instant(e.window_from, o.window_from) and _same_instant(e.due_at, o.due_at) for e in own)
        done = next((e for e in known if e in fulfils), None)
        if ok and len(known) == len(own):  # the whole history is known: the projection must agree with it
            ok = _same_instant(o.fulfilled_at, done.fulfilled_at if done else None) and \
                list(o.evidence_ids or []) == list((done.evidence_ids or []) if done else [])
        if not ok:
            res["problem"] = "OCCURRENCE_HISTORY_INCONSISTENT"
            continue
        res["disputed"] = res["disputed"] or any(e.event_type == "DISPUTED" for e in known)
        if done is not None:
            res["fulfilled_at"], res["evidence_ids"] = done.fulfilled_at, list(done.evidence_ids or [])
        res["source"] = BASELINE_SOURCE if known[0].event_type == _BASELINE and (done is None or done is known[0]) \
            else "EVENT_HISTORY"
    return out


def _explain(state, segments) -> str:
    n = len(segments)
    cnt = sum(1 for s in segments if s["assurance_state"] == state)
    if state == "PROVEN":
        return f"PROVEN because all {n} applicable segment(s) were proven."
    return f"{state} because {cnt} of {n} segment(s) were {state} (precedence FAILING > REQUIRES_REVIEW > NOT_PROVEN > PROVEN)."


def _coalesce(segments):
    out = []
    for s in segments:
        sig = (s["assurance_state"], tuple((l["control_key"], l.get("control_state"), tuple(l.get("reason_codes", [])))
                                           for l in s["leaf_evaluations"]), s["expression_version_id"],
               tuple(t["exception_id"] for t in s["exception_treatments"]))
        if out and out[-1]["_sig"] == sig and out[-1]["segment_range"][1] == s["segment_range"][0]:
            out[-1]["segment_range"] = [out[-1]["segment_range"][0], s["segment_range"][1]]
            out[-1]["coalesced_from"] += 1
            continue
        out.append({**s, "_sig": sig, "coalesced_from": 1})
    for s in out:
        s.pop("_sig")
    return out


def _issue_hooks(db, tenant_id, snap: AssuranceSnapshot, odef: ObligationDefinition):
    mapping = {"FAILING": ("Obligation FAILING", "OBLIGATION_FAILING", "HIGH", "P1"),
               "NOT_PROVEN": ("Proof gap", "PROOF_GAP", "MEDIUM", "P2"),
               "REQUIRES_REVIEW": ("Assurance requires review", "ASSURANCE_REVIEW", "HIGH", "P1")}
    if snap.state not in mapping:
        return
    title, itype, sev, pri = mapping[snap.state]
    key = f"{itype}:{snap.obligation_lineage_id}"
    if db.execute(select(Issue).where(Issue.tenant_id == tenant_id, Issue.dedupe_key == key,
                                      Issue.state.notin_(["CLOSED", "CANCELLED"]))).scalars().first():
        return
    iss = Issue(tenant_id=tenant_id, title=f"{title}: {odef.title}", issue_type=itype, severity=sev, priority=pri,
                source_ref_type="AssuranceSnapshot", source_ref_id=snap.snapshot_id, dedupe_key=key)
    db.add(iss)
    db.flush()
    from . import governance
    governance.record_issue_created(db, issue=iss, actor_id="system", reason=f"Proof gap: {snap.state}")
    if snap.state == "NOT_PROVEN":
        emit(db, event_type="assurance.proof_gap_detected", aggregate_type="AssuranceSnapshot",
             aggregate_id=snap.snapshot_id, tenant_id=tenant_id, payload={"reason_codes": snap.reason_codes})
