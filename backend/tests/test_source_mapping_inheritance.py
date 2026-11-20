"""RUL-REQ-008: source-mapping inheritance cannot be asserted by the author.

ZA-RUL-001 §08 branch coverage rule: "Every PredicateNode in a binding RuleVersion MUST have a source mapping directly or
inherit one from an enclosing legal branch with an explicit inheritance flag." §07 source_mapping_refs: "Each row maps to
at least one source/interpretation reference." §08: "Compiler-generated normalization nodes inherit origin source
mappings".

`inherits_mapping` is an ordinary field of the submitted AST. It used to be accepted on its own as proof that a
predicate was mapped, so an author could publish a binding predicate grounded in no source at all. It is now never proof:
a predicate is mapped only through a mapping its own node or an enclosing node actually carries. A row node needs its own
mapping in every authoring view. Malformed mapping references map nothing. Every draft path (validate, test, sandbox,
submit-review, shadow, publish) and the seed go through rules.validate, so none of them can bypass the rule.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select, text

from conftest import H, shadow_report_for
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"SMI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
REQUIRED = "RUL_SOURCE_MAPPING_REQUIRED"
S: dict = {}
_n = iter(range(10**6))

VECTORS = [
    {"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "APPLIES"},
    {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"}]


# ------------------------------------------------------------------------------------------------ AST builders
def pred(nid, *, mapping=None, flag=None, fact="entity.established_in_eu"):
    """An EQ predicate on a released fact; `mapping` is its own source_mappings value, `flag` its inherits_mapping."""
    value = {"type": "BOOLEAN", "value": True} if fact == "entity.established_in_eu" else {"type": "ENUM", "value": "CREDIT_INSTITUTION"}
    n = {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ",
         "args": [{"node_type": "FACT_REF", "node_id": f"{nid}_f", "fact": fact}, {"node_type": "CONST", "node_id": f"{nid}_c", **value}]}
    if mapping is not None:
        n["source_mappings"] = mapping
    if flag is not None:
        n["inherits_mapping"] = flag
    return n


def branch(nt, nid, children, mapping=None, **extra):
    n = {"node_type": nt, "node_id": nid, "children": children, **extra}
    if mapping is not None:
        n["source_mappings"] = mapping
    return n


def two_preds(tag, *, root_mapping=None, flag=None):
    """ALL(established_in_eu, financial_entity_type) — the shape the golden VECTORS exercise."""
    return branch("ALL", f"{tag}_root", [pred(f"{tag}_a", flag=flag),
                                        pred(f"{tag}_b", flag=flag, fact="entity.financial_entity_type")], root_mapping)


def table_ast(tag, *, root_mapping, row_mapping):
    """A decision table written directly as AST (authoring_mode AST): the compiler's shape, flags included."""
    row = branch("ALL", f"row_{tag}_R1", [pred(f"row_{tag}_R1_est", mapping=[], flag=True),
                                          pred(f"row_{tag}_R1_type", mapping=[], flag=True, fact="entity.financial_entity_type")],
                 row_mapping, row_id="R1")
    return branch("ANY", f"{tag}_dt", [row], root_mapping, decision_table=True)


def table(tag, *, root_mapping, row_mapping):
    """The same table in the decision-table authoring view."""
    row = {"row_id": "R1", "cells": {"est": True, "type": "CREDIT_INSTITUTION"}}
    if row_mapping is not None:
        row["source_mappings"] = row_mapping
    return {"decision_table_id": f"{tag}_dt", "table_mode": "ANY_MATCH", "source_mappings": root_mapping,
            "columns": [{"column_id": "est", "fact": "entity.established_in_eu", "operator": "EQ", "value_type": "BOOLEAN"},
                        {"column_id": "type", "fact": "entity.financial_entity_type", "operator": "EQ", "value_type": "ENUM"}],
            "rows": [row]}


def _validate(ast):
    from app.engines import rules
    return rules.validate(ast, S["fdefs"], S["sets"])


def _errors(rep, code=None):
    return sorted(i["node_id"] for i in rep["issues"] if i["severity"] == "ERROR" and (code is None or i["code"] == code))


# ------------------------------------------------------------------------------------------------ world + API flow
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        S["fdefs"], S["sets"] = appl.load_fact_defs(db), appl.load_sets(db)
        S["release_rows"] = _release_rows(db)  # every published rule version and release before this module runs
        db.add(Instrument(identifier=INSTR, title="Mapping Inheritance Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Mapping Inheritance Act",
        "locator": f"OJ L {TAG}/smi", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"SMI-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 2", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SMI_{TAG}", title="Mapping inheritance obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


def _release_rows(db) -> dict:
    from app.platform import digest
    out = {}
    for t, k in (("catalog_rule_version", "rule_version_id"), ("catalog_content_release", "content_release_id")):
        for r in db.execute(text(f"SELECT * FROM {t}")).mappings():
            out[(t, r[k])] = digest({x: str(y) for x, y in r.items()})
    return out


def _records(tag, ids) -> list[dict]:
    """Valid, grounded DIRECT_TEXT mapping records sm_<tag>_<id> for each id."""
    p = S["rv"]["provisions"][0]
    return [{"source_mapping_id": f"sm_{tag}_{i}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT"} for i in ids]


def _create(client, *, ast=None, decision_table=None, record_ids=("root", "a", "b"), tag) -> str:
    body = {"obligation_definition_id": S["odef"], "golden_vectors": VECTORS, "valid_from": "2025-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": _records(tag, record_ids)}
    body |= {"authoring_mode": "DECISION_TABLE", "decision_table": decision_table} if decision_table else \
        {"authoring_mode": "AST", "ast": ast}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert d.status_code == 200, d.text
    return d.json()["draft_id"]


def _review_and_publish(client, did):
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"smi-{did[:8]}", "changelog": "probe"})
    assert r.status_code == 200, r.text
    S.setdefault("published", []).append(r.json())
    return r.json()


def _as_if_verified_before_the_fix(did):
    """Put a draft in the state a draft verified under the old validator would be in: VERIFIED, reviewed by distinct
    people, shadow completed. Only the final gates (shadow, publish) re-validate, so they must refuse it."""
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        d.status, d.peer_reviewer_id, d.legal_reviewer_id = "VERIFIED", PEER, LEGAL
        d.shadow_report = shadow_report_for(db, d)
        db.commit()


def _state(did):
    from app.db import admin_engine
    with admin_engine.connect() as c:
        return {"releases": c.execute(text("SELECT count(*) FROM catalog_content_release")).scalar(),
                "rule_versions": c.execute(text("SELECT count(*) FROM catalog_rule_version")).scalar(),
                "audit": c.execute(text("SELECT count(*) FROM platform_audit_event")).scalar(),
                "outbox": c.execute(text("SELECT count(*) FROM platform_outbox_event")).scalar(),
                "record_seq": c.execute(text("SELECT current_seq FROM platform_record_sequence")).scalar(),
                "draft": c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).scalar()}


# ------------------------------------------------------------------------------------------------ 1. the flag is not proof
@pytest.mark.parametrize("shape", ["lone_predicate", "under_unmapped_all", "under_unmapped_not", "flagged_branches",
                                   "empty_own_mapping"])
def test_author_set_inherits_mapping_cannot_bypass_mapping(world, shape):
    """The original bypass: inherits_mapping on a predicate with no mapping on it or anywhere above it."""
    if shape == "lone_predicate":
        ast, offenders = pred("x_p", flag=True), ["x_p"]
    elif shape == "under_unmapped_all":
        ast, offenders = two_preds("x", flag=True), ["x_a", "x_b"]
    elif shape == "under_unmapped_not":
        ast, offenders = {"node_type": "NOT", "node_id": "x_not", "child": two_preds("x", flag=True)}, ["x_a", "x_b"]
    elif shape == "flagged_branches":  # a flag on the enclosing branches carries no mapping down either
        inner = two_preds("x", flag=True)
        inner["inherits_mapping"] = True
        ast, offenders = branch("ANY", "x_any", [inner, pred("x_c", flag=True)], inherits_mapping=True), ["x_a", "x_b", "x_c"]
    else:
        ast = two_preds("x", flag=True)
        for c in ast["children"]:
            c["source_mappings"] = []
        offenders = ["x_a", "x_b"]
    rep = _validate(ast)
    assert not rep["ok"]
    assert _errors(rep, REQUIRED) == offenders
    assert all("inherits_mapping" in i["message"] for i in rep["issues"] if i["code"] == REQUIRED)


# ------------------------------------------------------------------------------------------------ 2. no mapping anywhere
def test_predicate_without_mapping_or_mapped_ancestor_is_rejected(world):
    rep = _validate(two_preds("y"))
    assert _errors(rep) == ["y_a", "y_b"] and _errors(rep, REQUIRED) == ["y_a", "y_b"]
    assert {i["message"] for i in rep["issues"] if i["code"] == REQUIRED} == {"Predicate lacks legal source mapping."}


@pytest.mark.parametrize("bad", ["sm_whole_string", [""], ["   "], [None], [{}], [7], {"id": "sm"}, 5, True])
def test_malformed_mapping_references_map_nothing(world, bad):
    """Only a list of source-mapping IDs maps a branch; anything else is a schema error and covers no predicate."""
    rep = _validate(two_preds("z", root_mapping=bad, flag=True))
    assert ("RUL_SCHEMA_INVALID", "z_root") in {(i["code"], i["node_id"]) for i in rep["issues"]}
    assert _errors(rep, REQUIRED) == ["z_a", "z_b"]
    own = _validate(branch("ALL", "z_root", [pred("z_a", mapping=bad), pred("z_b", mapping=["sm_ok"])]))
    assert ("RUL_SCHEMA_INVALID", "z_a") in {(i["code"], i["node_id"]) for i in own["issues"]}
    assert _errors(own, REQUIRED) == ["z_a"]


# ------------------------------------------------------------------------------------------------ 3. legitimate inheritance
@pytest.mark.parametrize("flag", [None, True, False])
def test_predicate_inherits_from_a_mapped_enclosing_branch(world, flag):
    """With the explicit flag (§08) or, as the seeded catalogue does, without it."""
    rep = _validate(two_preds("w", root_mapping=["sm_w_root"], flag=flag))
    assert rep["ok"], rep["issues"]


def test_direct_mappings_need_no_enclosing_branch(world):
    rep = _validate(branch("ALL", "v_root", [pred("v_a", mapping=["sm_v_a"]),
                                             pred("v_b", mapping=["sm_v_b"], fact="entity.financial_entity_type")]))
    assert rep["ok"], rep["issues"]


# ------------------------------------------------------------------------------------------------ 4. compiler inheritance
def test_compiler_generated_inheritance_comes_from_the_row_mapping(world):
    from app.engines import rules
    ast, issues = rules.compile_decision_table(table("c", root_mapping=["sm_c_root"], row_mapping=["sm_c_r1"]))
    assert issues == []
    # The compiler's output is unchanged (byte-stable canonical form, RUL-REQ-016): generated predicates keep their
    # empty mappings and flag, and the row node carries the row's source mapping they inherit.
    assert ast == {"node_type": "ANY", "node_id": "c_dt", "source_mappings": ["sm_c_root"], "decision_table": True, "children": [
        {"node_type": "ALL", "node_id": "row_R1", "row_id": "R1", "source_mappings": ["sm_c_r1"], "children": [
            {"node_type": "PREDICATE", "node_id": "row_R1_est", "operator": "EQ", "source_mappings": [], "inherits_mapping": True,
             "args": [{"node_type": "FACT_REF", "node_id": "row_R1_est_fact", "fact": "entity.established_in_eu"},
                      {"node_type": "CONST", "node_id": "row_R1_est_val", "type": "BOOLEAN", "value": True}]},
            {"node_type": "PREDICATE", "node_id": "row_R1_type", "operator": "EQ", "source_mappings": [], "inherits_mapping": True,
             "args": [{"node_type": "FACT_REF", "node_id": "row_R1_type_fact", "fact": "entity.financial_entity_type"},
                      {"node_type": "CONST", "node_id": "row_R1_type_val", "type": "ENUM", "value": "CREDIT_INSTITUTION"}]}]}]}
    rep = _validate(ast)
    assert rep["ok"], rep["issues"]
    from app.engines.catalog import _walk
    assert {n["node_id"]: refs for n, refs in _walk(ast) if n["node_type"] == "PREDICATE"} == \
        {"row_R1_est": ("sm_c_r1",), "row_R1_type": ("sm_c_r1",)}


def test_seeded_decision_table_still_validates(world):
    """The seeded DORA scope table (decision-table view) compiles and validates exactly as before."""
    from app.db import AdminSession
    from app.engines import rules
    from app.models import RuleDraft
    with AdminSession() as db:
        tables = [d.decision_table for d in db.execute(select(RuleDraft).where(RuleDraft.authoring_mode == "DECISION_TABLE")).scalars()
                  if d.decision_table and d.decision_table.get("decision_table_id") == "dt_dora_scope"]
    assert tables
    ast, issues = rules.compile_decision_table(tables[0])
    assert issues == [] and _validate(ast)["ok"]


# ------------------------------------------------------------------------------------------------ 5. decision-table rows
def test_unmapped_row_is_refused_in_both_authoring_views(world, client):
    """§07: each row maps to at least one source reference, even when the table root is mapped. Written as AST, the
    compiler never runs, so validation itself enforces the row rule; in the table view the one issue is reported once."""
    as_ast = _validate(table_ast("t", root_mapping=["sm_t_root"], row_mapping=None))
    assert _errors(as_ast) == ["row_t_R1"] and _errors(as_ast, REQUIRED) == ["row_t_R1"]
    unmapped_root = _validate(table_ast("t", root_mapping=None, row_mapping=None))  # flags inherit nothing either
    assert _errors(unmapped_root, REQUIRED) == ["row_t_R1", "row_t_R1_est", "row_t_R1_type"]
    assert _validate(table_ast("t", root_mapping=None, row_mapping=["sm_t_r1"]))["ok"]

    tag = f"dt{next(_n)}"
    did = _create(client, decision_table=table(tag, root_mapping=[f"sm_{tag}_root"], row_mapping=None), tag=tag)
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
    assert not rep["ok"]
    assert [(i["code"], i["node_id"]) for i in rep["issues"] if i["severity"] == "ERROR"] == [(REQUIRED, "row_R1")]


# ------------------------------------------------------------------------------------------------ 6. nesting
def _nested(tag, *, b2_mapping=None):
    """ALL(ANY[b1 mapped](ALL(p1 flag, p2), p3 own), NOT(ANY[b2](p4 flag, p5)))."""
    b1 = branch("ANY", f"{tag}_b1", [branch("ALL", f"{tag}_b1x", [pred(f"{tag}_p1", flag=True), pred(f"{tag}_p2")]),
                                     pred(f"{tag}_p3", mapping=[f"sm_{tag}_p3"])], [f"sm_{tag}_b1"])
    b2 = branch("ANY", f"{tag}_b2", [pred(f"{tag}_p4", flag=True), pred(f"{tag}_p5")], b2_mapping)
    return branch("ALL", f"{tag}_root", [b1, {"node_type": "NOT", "node_id": f"{tag}_not", "child": b2}])


def test_nested_branches_resolve_to_the_nearest_mapped_ancestor(world):
    from app.engines.catalog import _walk
    rep = _validate(_nested("n"))
    assert _errors(rep) == ["n_p4", "n_p5"] and _errors(rep, REQUIRED) == ["n_p4", "n_p5"]
    ok = _nested("n", b2_mapping=["sm_n_b2"])
    assert _validate(ok)["ok"]
    assert {n["node_id"]: refs for n, refs in _walk(ok) if n["node_type"] == "PREDICATE"} == {
        "n_p1": ("sm_n_b1",), "n_p2": ("sm_n_b1",), "n_p3": ("sm_n_p3",), "n_p4": ("sm_n_b2",), "n_p5": ("sm_n_b2",)}


# ------------------------------------------------------------------------------------------------ 7. valid content publishes
@pytest.mark.parametrize("case", ["inherit_without_flag", "inherit_with_flag", "direct", "nested", "decision_table"])
def test_valid_source_mappings_remain_publishable(client, world, case):
    tag = f"ok{next(_n)}"
    if case == "decision_table":
        did = _create(client, decision_table=table(tag, root_mapping=[f"sm_{tag}_root"], row_mapping=[f"sm_{tag}_r1"]),
                      record_ids=("root", "r1"), tag=tag)
    elif case == "direct":
        did = _create(client, ast=branch("ALL", f"{tag}_root", [pred(f"{tag}_a", mapping=[f"sm_{tag}_a"]),
                                                                pred(f"{tag}_b", mapping=[f"sm_{tag}_b"],
                                                                     fact="entity.financial_entity_type")]), tag=tag)
    elif case == "nested":
        ast = branch("ALL", f"{tag}_root", [branch("ANY", f"{tag}_mid", [
            branch("ALL", f"{tag}_in", [pred(f"{tag}_a", flag=True), pred(f"{tag}_a2")]), pred(f"{tag}_a3", flag=True)],
            [f"sm_{tag}_a"]), pred(f"{tag}_b", mapping=[f"sm_{tag}_b"], fact="entity.financial_entity_type")])
        did = _create(client, ast=ast, record_ids=("a", "b"), tag=tag)
    else:
        did = _create(client, ast=two_preds(tag, root_mapping=[f"sm_{tag}_root"],
                                            flag=True if case == "inherit_with_flag" else None), record_ids=("root",), tag=tag)
    out = _review_and_publish(client, did)
    assert out["rule_version"]["status"] == "PUBLISHED"


# ------------------------------------------------------------------------------------------------ 8 + 9. every path refuses
BYPASSES = {"flag_without_mapping": lambda tag: two_preds(tag, flag=True),
            "table_rows_without_mapping": lambda tag: table_ast(tag, root_mapping=[f"sm_{tag}_root"], row_mapping=None)}


@pytest.mark.parametrize("bypass", sorted(BYPASSES))
def test_no_draft_path_accepts_the_bypass(client, world, bypass):
    """Both ASTs passed the old validator. Now validate, test and sandbox report them, submit-review refuses them, and a
    draft already VERIFIED under the old validator is refused by shadow and by publish without writing anything."""
    tag = f"by{next(_n)}"
    did = _create(client, ast=BYPASSES[bypass](tag), tag=tag)
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert rep.status_code == 200 and not rep.json()["ok"]
    assert REQUIRED in {i["code"] for i in rep.json()["issues"]}
    assert client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR)).status_code == 422
    assert client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR),
                       json={"facts": VECTORS[0]["facts"]}).status_code == 422
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and _state(did)["draft"] == "VALIDATION_FAILED"

    _as_if_verified_before_the_fix(did)
    before, fingerprint = _state(did), db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 409 and r.json()["detail"]["message"] == "Validation errors block shadow impact"
    assert r.json()["detail"]["error"] == REQUIRED
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"smi-{did[:8]}", "changelog": "x"})
    assert r.status_code == 409 and "Validation failure blocks publication" in r.text
    assert _state(did) == before and before["draft"] == "VERIFIED"
    assert db_fingerprint() == fingerprint


def test_seed_publication_path_refuses_the_bypass(world):
    """app/seed.py publishes through rules.validate (and asserts it is ok): the same validator refuses the bypass."""
    from app.engines import rules
    for make in BYPASSES.values():
        assert not rules.validate(make("seed"), S["fdefs"], S["sets"])["ok"]


# ------------------------------------------------------------------------------------------------ 10. releases unchanged
def test_existing_published_releases_are_unchanged_and_still_valid(world):
    """Every rule version and release published before this module is byte-identical afterwards, and every seeded
    published rule still validates: legitimate inherited mappings are preserved."""
    from app.db import AdminSession
    from app.engines import rules
    from app.models import Instrument, ObligationDefinition, RuleVersion
    with AdminSession() as db:
        now = _release_rows(db)
        seeded = db.execute(select(RuleVersion).join(ObligationDefinition, ObligationDefinition.obligation_definition_id
                                                     == RuleVersion.obligation_definition_id)
                            .join(Instrument, Instrument.instrument_id == ObligationDefinition.instrument_id)
                            .where(Instrument.identifier.in_(("EU-2024-1689", "EU-2022-2554", "UK-GDPR", "EU-2022-2555",
                                                              "FCA-PRIN-2A")))).scalars().all()
        mine = [db.get(RuleVersion, p["rule_version"]["rule_version_id"]) for p in S.get("published", [])]
        checks = [(rv.rule_version_id, rules.validate(rv.canonical_ast, S["fdefs"], S["sets"])) for rv in seeded + mine]
    assert S["release_rows"] and {k: now.get(k) for k in S["release_rows"]} == S["release_rows"]
    assert seeded and len(mine) == 5
    assert [(rv, rep["issues"]) for rv, rep in checks if not rep["ok"]] == []
