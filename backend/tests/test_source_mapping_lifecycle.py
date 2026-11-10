"""Source-mapping validation across the rule lifecycle (RUL-REQ-008, ZA-RUL-001 §§07, 08, 10, 11, 15, 18).

Contract, from the controlled text:
  §11 stage 1 "Schema parse ... unknown node/operator/field rejected"; stage 2 "Reference resolution: FactDefinitions,
  SetVersions, obligation/requirement/source mappings resolve and are release-compatible" -- static validation.
  §10 DRAFT -> STATIC VALIDATION -> PEER REVIEW -> LEGAL REVIEW -> ... ; VALIDATION_FAILED "cannot advance".
  §18 ERROR "Blocks review/release"; WF-RUL-01 "ERROR blocks review".
  §08 requirement_version_id / provision_id "Exact source-grounded content objects"; source_fingerprint of the source
  version; the field rules.
  §15 / INV-06 / INV-09: the release gate (definition provision links, and content VERIFIED before it reaches RELEASED).

So the draft's mappings are resolved at static validation -- /validate, submit-review, peer and legal approval and
shadow, on the current persisted content -- and at publication (unchanged RELEASE_CONTENT_NOT_VERIFIED gate):
  * AST references (unresolved, malformed), record shape (malformed, missing/duplicate ID), grounding, requirement
    version and provision existence, provision/requirement relationship, instrument, source fingerprint, §08 fields.
Publication only (release-compatibility of the cited content, kept where the established workflow has it):
  * the cited requirement version / provision being VERIFIED or PUBLISHED (RULE_SOURCE_MAPPING_NOT_VERIFIED).
The golden-vector workbench (/test, /sandbox) runs on an unreviewed DRAFT ("Can run sandbox vectors only", §10) and is
not a lifecycle stage; it validates the AST only.
"""
from __future__ import annotations

import secrets
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, update

from conftest import H
from test_authorization import db_fingerprint
from test_source_mapping_inheritance import VECTORS, pred, table

TAG = secrets.token_hex(3).upper()
INSTR, OTHER_INSTR = f"SML-{TAG}-REG", f"SML-{TAG}-OTHER"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
S: dict = {}
_n = iter(range(10**6))


# ------------------------------------------------------------------------------------------------ world
def _source(client, instr, title):
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": title,
        "locator": f"OJ L {TAG}/{title[:6]}", "instrument_identifier": instr, "raw_text": f"{title}: keep a register."})
    assert r.status_code == 200, r.text
    return r.json()


def _requirement(client, source, instr, key, verify=True):
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": instr, "requirement_key": key, "title": "Register",
        "locator": "Art. 2", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    if verify:
        assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        for ident in (INSTR, OTHER_INSTR):
            db.add(Instrument(identifier=ident, title=f"Lifecycle {ident}", jurisdiction="EU", authority="Authority",
                              coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    S["source"], S["other_source"] = _source(client, INSTR, "Lifecycle Act"), _source(client, OTHER_INSTR, "Other Act")
    S["rv"] = _requirement(client, S["source"], INSTR, f"SML-{TAG}-REQ")
    S["rv2"] = _requirement(client, S["source"], INSTR, f"SML-{TAG}-REQ2")          # same instrument, other version
    S["rv_unverified"] = _requirement(client, S["source"], INSTR, f"SML-{TAG}-DRAFT", verify=False)
    S["rv_other"] = _requirement(client, S["other_source"], OTHER_INSTR, f"SML-{TAG}-OTHER")
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SML_{TAG}", title="Lifecycle obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


# ------------------------------------------------------------------------------------------------ content
def rec(tag, mid="root", rv_key="rv", **over) -> dict:
    """A valid, grounded DIRECT_TEXT mapping record; `over` replaces fields (`...` removes one)."""
    rv = S[rv_key]
    p = rv["provisions"][0]
    m = {"source_mapping_id": f"sm_{tag}_{mid}", "requirement_version_id": rv["requirement_version_id"],
         "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
         "source_fingerprint": (S["other_source"] if rv_key == "rv_other" else S["source"])["fingerprint"],
         "mapping_type": "DIRECT_TEXT"}
    for k, v in over.items():
        if v is ...:
            m.pop(k, None)
        else:
            m[k] = v
    return m


def ast(tag, root_mapping=None):
    """ALL(established_in_eu, financial_entity_type) inheriting the root's mapping sm_<tag>_root."""
    return {"node_type": "ALL", "node_id": f"{tag}_root",
            "source_mappings": [f"sm_{tag}_root"] if root_mapping is None else root_mapping,
            "children": [pred(f"{tag}_a"), pred(f"{tag}_b", fact="entity.financial_entity_type")]}


# name -> (ast factory, records factory, expected first ERROR code, the mapping it concerns or None)
CATEGORIES = {
    "unresolved_reference": (ast, lambda t: [rec(t, "other")], "RULE_SOURCE_MAPPING_UNRESOLVED", "root"),
    "malformed_reference": (lambda t: ast(t, root_mapping=f"sm_{t}_root"), lambda t: [rec(t)], "RUL_SCHEMA_INVALID", None),
    "malformed_record": (ast, lambda t: [rec(t), "srcmap-as-text"], "RULE_SOURCE_MAPPING_MALFORMED", None),
    "missing_mapping_id": (ast, lambda t: [rec(t), rec(t, "x", source_mapping_id=...)], "RULE_SOURCE_MAPPING_ID_MISSING", None),
    "duplicate_mapping_id": (ast, lambda t: [rec(t), rec(t)], "RULE_SOURCE_MAPPING_DUPLICATE", "root"),
    "requirement_missing": (ast, lambda t: [rec(t, requirement_version_id="rv-does-not-exist", provision_id=None)],
                            "RULE_SOURCE_MAPPING_REQUIREMENT_MISSING", "root"),
    "provision_missing": (ast, lambda t: [rec(t, provision_id="provision-does-not-exist")], "RULE_SOURCE_MAPPING_PROVISION_MISSING", "root"),
    "provision_mismatch": (ast, lambda t: [rec(t, provision_id=S["rv2"]["provisions"][0]["provision_id"])],
                           "RULE_SOURCE_MAPPING_PROVISION_MISMATCH", "root"),
    "wrong_instrument": (ast, lambda t: [rec(t, rv_key="rv_other")], "RULE_SOURCE_MAPPING_INSTRUMENT_MISMATCH", "root"),
    "fingerprint_mismatch": (ast, lambda t: [rec(t, source_fingerprint="f" * 64)], "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH", "root"),
    "ungrounded": (ast, lambda t: [rec(t, requirement_version_id=None, provision_id=None)], "RULE_SOURCE_MAPPING_UNGROUNDED", "root"),
    "mapping_type_invalid": (ast, lambda t: [rec(t, mapping_type="GUESS")], "RULE_SOURCE_MAPPING_TYPE_INVALID", "root"),
    "rationale_missing": (ast, lambda t: [rec(t, mapping_type="INTERPRETIVE")], "RULE_SOURCE_MAPPING_RATIONALE_REQUIRED", "root"),
    "reference_missing": (ast, lambda t: [rec(t, authoritative_reference=" ")], "RULE_SOURCE_MAPPING_REFERENCE_REQUIRED", "root"),
}


def _create(client, tag, *, ast_=None, records=None, decision_table=None) -> str:
    body = {"obligation_definition_id": S["odef"], "golden_vectors": VECTORS, "valid_from": "2025-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": records if records is not None else [rec(tag)]}
    body |= {"authoring_mode": "DECISION_TABLE", "decision_table": decision_table} if decision_table is not None else \
        {"authoring_mode": "AST", "ast": ast_ if ast_ is not None else ast(tag)}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


STEPS = (("submit-review", AUTHOR), ("peer-approve", PEER), ("legal-approve", LEGAL), ("shadow", RELEASE))


def _advance(client, did, upto: str):
    """Run the lifecycle steps before `upto` (one of the STEPS names or "publish")."""
    for step, actor in STEPS:
        if step == upto:
            return
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)


def _replace(did, **content):
    """Change stored content below the API (status, reviewers and stored report untouched)."""
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        for k, v in content.items():
            setattr(d, k, v)
        db.commit()


def _counts() -> dict:
    from app.db import admin_engine
    with admin_engine.connect() as c:
        return {"audit": c.execute(text("SELECT count(*) FROM platform_audit_event")).scalar(),
                "outbox": c.execute(text("SELECT count(*) FROM platform_outbox_event")).scalar(),
                "record_seq": c.execute(text("SELECT current_seq FROM platform_record_sequence")).scalar()}


def _draft(did) -> dict:
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        return {"status": d.status, "peer": d.peer_reviewer_id, "legal": d.legal_reviewer_id, "revision": d.revision}


def _errors(issues):
    return [i for i in issues if i["severity"] == "ERROR"]


# ------------------------------------------------------------------------------------------------ validate + submit-review
@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_static_validation_resolves_mappings_at_validate_and_submit_review(client, world, category):
    """/validate reports the problem as an ERROR issue (code, source_mapping_id, the citing node) and records
    VALIDATION_FAILED with its rule.validation_failed event, as for any validation error. submit-review refuses with the
    structured 409 and writes only VALIDATION_FAILED and the report: no review, approval, audit, event or sequence."""
    make_ast, make_records, code, mapping = CATEGORIES[category]
    tag = f"v{next(_n)}"
    did = _create(client, tag, ast_=make_ast(tag), records=make_records(tag))
    before = _counts()
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert rep.status_code == 200 and rep.json()["ok"] is False
    issue = _errors(rep.json()["issues"])[0]
    assert issue["code"] == code, rep.json()["issues"]
    if mapping:
        assert issue["source_mapping_id"] == f"sm_{tag}_{mapping}" and issue["node_id"] == f"{tag}_root"
        assert issue["node_ids"] == [f"{tag}_root"]
    after_validate = _counts()
    assert after_validate == {**before, "outbox": before["outbox"] + 1, "record_seq": before["record_seq"] + 1}
    assert _draft(did)["status"] == "VALIDATION_FAILED"

    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == code and detail["message"] == "Validation errors block review"
    assert _errors(detail["validation"]["issues"])[0] == issue
    assert _counts() == after_validate
    assert _draft(did) == {"status": "VALIDATION_FAILED", "peer": None, "legal": None, "revision": 1}


# ------------------------------------------------------------------------------------------------ approvals, shadow, publish
STAGE_CATEGORIES = ("fingerprint_mismatch", "wrong_instrument", "provision_mismatch", "unresolved_reference")


@pytest.mark.parametrize("category", STAGE_CATEGORIES)
@pytest.mark.parametrize("stage", ["peer-approve", "legal-approve", "shadow", "publish"])
def test_every_later_stage_revalidates_the_current_mappings(client, world, stage, category):
    """A draft valid when it reached `stage`, whose stored mappings are now invalid: approvals and shadow refuse with
    the structured validation error, publication with its unchanged RELEASE_CONTENT_NOT_VERIFIED gate. Nothing written."""
    make_ast, make_records, code, _ = CATEGORIES[category]
    tag = f"st{next(_n)}"
    did = _create(client, tag)
    _advance(client, did, stage)
    _replace(did, ast=make_ast(tag), source_mappings=make_records(tag))
    state, fingerprint = _draft(did), db_fingerprint()
    actor = {"peer-approve": PEER, "legal-approve": LEGAL, "shadow": RELEASE, "publish": RELEASE}[stage]
    body = {"json": {"label": f"sml-{did[:8]}", "changelog": "x"}} if stage == "publish" else {}
    r = client.post(f"/api/rules/drafts/{did}/{stage}", headers=H(actor), **body)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    if stage == "publish":
        assert detail["error"] == "RELEASE_CONTENT_NOT_VERIFIED" and code in {p["code"] for p in detail["problems"]}
    else:
        assert detail["error"] == code and detail["message"].startswith("Validation errors block")
    assert _draft(did) == state and db_fingerprint() == fingerprint


# ------------------------------------------------------------------------------------------------ release-only check
def test_unverified_requirement_passes_review_and_is_refused_only_at_publication(client, world):
    """Whether the cited content is VERIFIED is the publication gate's (INV-09), as in the established workflow: the
    draft is reviewed while its requirement is under verification and publishes once it is verified."""
    tag = f"unv{next(_n)}"
    did = _create(client, tag, records=[rec(tag, rv_key="rv_unverified", source_fingerprint=S["source"]["fingerprint"])])
    assert client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()["ok"] is True
    _advance(client, did, "publish")
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"sml-{did[:8]}", "changelog": "x"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RELEASE_CONTENT_NOT_VERIFIED"
    assert {p["code"] for p in r.json()["detail"]["problems"]} == {"RULE_SOURCE_MAPPING_NOT_VERIFIED"}
    assert db_fingerprint() == before
    rv_id = S["rv_unverified"]["requirement_version_id"]
    assert client.post(f"/api/catalog/requirements/{rv_id}/verify", headers=H(LEGAL)).status_code == 200
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"sml-{did[:8]}b", "changelog": "x"})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------------ valid content
@pytest.mark.parametrize("mode", ["AST", "DECISION_TABLE"])
def test_valid_mappings_are_accepted_at_every_stage(client, world, mode):
    tag = f"ok{next(_n)}"
    if mode == "DECISION_TABLE":
        did = _create(client, tag, decision_table=table(tag, root_mapping=[f"sm_{tag}_root"], row_mapping=[f"sm_{tag}_r1"]),
                      records=[rec(tag), rec(tag, "r1", provision_id=S["rv"]["provisions"][0]["provision_id"])])
    else:
        did = _create(client, tag, records=[rec(tag), rec(tag, "spare", mapping_type="CROSS_REFERENCE",
                                                         interpretation_rationale="Cross-reference to Art. 2(1).")])
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
    assert rep["ok"] is True and _errors(rep["issues"]) == []
    _advance(client, did, "publish")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"sml-{did[:8]}", "changelog": "x"})
    assert r.status_code == 200, r.text


def test_golden_vector_workbench_runs_before_mappings_resolve(client, world):
    """§10: a DRAFT "Can run sandbox vectors only". /test and /sandbox validate the AST, not the mappings."""
    tag = f"wb{next(_n)}"
    did = _create(client, tag, records=[rec(tag, source_fingerprint="0" * 64)])
    assert client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()["ok"] is False
    r = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR))
    assert r.status_code == 200 and r.json()["all_passed"] is True, r.text
    r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR), json={"facts": VECTORS[0]["facts"]})
    assert r.status_code == 200 and r.json()["outcome"] == "APPLIES", r.text


def test_seeded_rules_satisfy_static_and_release_mapping_validation(client, world):
    from types import SimpleNamespace
    from app.db import AdminSession
    from app.engines import catalog, rules
    from app.models import Instrument, ObligationDefinition, RuleDraft, RuleVersion
    from app.routers.rules import _validate
    seeded = ("EU-2024-1689", "EU-2022-2554", "UK-GDPR", "EU-2022-2555", "FCA-PRIN-2A")
    with AdminSession() as db:
        pairs = db.execute(select(RuleDraft, RuleVersion).join(RuleVersion, RuleVersion.draft_id == RuleDraft.draft_id)
                           .join(ObligationDefinition, ObligationDefinition.obligation_definition_id == RuleVersion.obligation_definition_id)
                           .join(Instrument, Instrument.instrument_id == ObligationDefinition.instrument_id)
                           .where(Instrument.identifier.in_(seeded))).all()
        out = [(d.draft_id, _validate(SimpleNamespace(db=db), d)["issues"],
                catalog.draft_source_mapping_problems(db, d, rules.canonicalize(rv.canonical_ast), release=True)) for d, rv in pairs]
    assert out
    assert [(did, _errors(issues), problems) for did, issues, problems in out if _errors(issues) or problems] == []


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL)
def _call_in_thread(route, actor, did, out):
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.security import Ctx
    with SessionLocal() as db:
        set_principal(db, None, actor)
        db.execute(text("SET lock_timeout = '30s'"))
        try:
            route(did, Ctx(db=db, actor=db.get(Actor, actor)))
            out["status"] = 200
        except HTTPException as e:
            out["status"], out["detail"] = e.status_code, e.detail
            db.rollback()


@pytest.mark.parametrize("stage", ["submit-review", "peer-approve"])
def test_concurrent_mapping_edit_is_validated_before_the_transition(client, world, stage):
    """An uncommitted change makes the draft's mappings invalid. submit-review / peer approval waits for the draft row,
    then validates the committed mappings and refuses: no transition rests on mappings other than those validated."""
    from app.db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.models import RuleDraft
    from app.routers.rules import peer_approve, submit_review
    tag = f"race{next(_n)}"
    did = _create(client, tag)
    _advance(client, did, stage)
    out: dict = {}
    route, actor = (submit_review, AUTHOR) if stage == "submit-review" else (peer_approve, PEER)
    with admin_engine.connect() as c:
        tx = c.begin()
        c.execute(update(RuleDraft.__table__).where(RuleDraft.__table__.c.draft_id == did)
                  .values(source_mappings=[rec(tag, source_fingerprint="e" * 64)]))
        worker = threading.Thread(target=_call_in_thread, args=(route, actor, did, out))
        worker.start()
        worker.join(1.5)
        assert worker.is_alive() and not out, "the transition must wait for the draft row while the edit is uncommitted"
        tx.commit()
    worker.join(30)
    assert not worker.is_alive()
    assert out["status"] == 409 and out["detail"]["error"] == "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH", out
    state = _draft(did)
    assert state["peer"] is None and state["legal"] is None
    assert state["status"] == ("VALIDATION_FAILED" if stage == "submit-review" else "IN_REVIEW")
