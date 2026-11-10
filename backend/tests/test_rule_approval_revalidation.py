"""RUL-REQ-008 at review: peer and legal approval revalidate the draft's current content.

ZA-RUL-001 §10: VALIDATION_FAILED "Static/type/lint error; cannot advance"; the Qualified Legal Reviewer may "Approve/
reject interpretation and source mapping" but may not "Bypass technical validation". Events: rule.review_requested
"Exact revision frozen for reviewer".

An IN_REVIEW status, a stored validation report or an earlier approval proves nothing about the draft's current content:
a draft submitted before inherits_mapping stopped counting as a mapping, or changed after it was validated, must not reach
VERIFIED through an approval. Both approval routes lock the draft row, revalidate it with the validation shadow and
publish use (routers/rules._validate -> rules.validate) and refuse with HTTP 409 {"error": <first ZA-RUL-001 §18 error
code>, "message", "validation": <report whose issues carry node_id>}, writing nothing.
"""
from __future__ import annotations

import json
import secrets
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, update

from conftest import H
from test_authorization import db_fingerprint
from test_source_mapping_inheritance import VECTORS, branch, pred, table, table_ast, two_preds

TAG = secrets.token_hex(3).upper()
INSTR = f"RAR-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
REQUIRED, SCHEMA = "RUL_SOURCE_MAPPING_REQUIRED", "RUL_SCHEMA_INVALID"
S: dict = {}
_n = iter(range(10**6))


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Approval Revalidation Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Approval Act",
        "locator": f"OJ L {TAG}/rar", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RAR-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 2", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RAR_{TAG}", title="Approval revalidation obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


def _records(tag) -> list[dict]:
    p = S["rv"]["provisions"][0]
    return [{"source_mapping_id": f"sm_{tag}_{i}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT"} for i in ("root", "a", "b", "r1")]


def _body(tag, *, ast=None, decision_table=None) -> dict:
    body = {"obligation_definition_id": S["odef"], "golden_vectors": VECTORS, "valid_from": "2025-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": _records(tag)}
    return body | ({"authoring_mode": "DECISION_TABLE", "decision_table": decision_table} if decision_table is not None
                   else {"authoring_mode": "AST", "ast": ast})


def _valid_ast(tag):
    return two_preds(tag, root_mapping=[f"sm_{tag}_root"])


def _valid_table(tag):
    return table(tag, root_mapping=[f"sm_{tag}_root"], row_mapping=[f"sm_{tag}_r1"])


def _submitted(client, tag, *, decision_table=False) -> str:
    """A valid draft, submitted for review (so its stored validation report says ok)."""
    body = _body(tag, decision_table=_valid_table(tag)) if decision_table else _body(tag, ast=_valid_ast(tag))
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    return did


JSON_COLUMNS = ("ast", "decision_table", "source_mappings", "golden_vectors", "validation_report", "vector_report", "shadow_report")


def _draft_row(did) -> dict:
    """The stored draft row (JSON columns decoded: SQLite returns them as text to a raw query, PostgreSQL as objects)."""
    from app.db import admin_engine
    with admin_engine.connect() as c:
        row = dict(c.execute(text("SELECT * FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).mappings().one())
    return {k: json.loads(v) if k in JSON_COLUMNS and isinstance(v, str) else v for k, v in row.items()}


def _replace_content(did, **content):
    """Change the stored content below the API, leaving status, reviewers and the stored report as they were: the state
    of a draft validated under an older validator, or changed by a path that does not reset its reviews."""
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        for k, v in content.items():
            setattr(d, k, v)
        db.commit()


def _state(did) -> dict:
    from app.db import admin_engine
    with admin_engine.connect() as c:
        return {"audit": c.execute(text("SELECT count(*) FROM platform_audit_event")).scalar(),
                "outbox": c.execute(text("SELECT count(*) FROM platform_outbox_event")).scalar(),
                "record_seq": c.execute(text("SELECT current_seq FROM platform_record_sequence")).scalar(),
                "draft": {k: v for k, v in _draft_row(did).items()}}


def _refused(client, did, step, actor, expected) -> dict:
    """The approval is refused with the structured validation error and writes nothing."""
    before, fingerprint = _state(did), db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    errors = [(i["code"], i["node_id"]) for i in detail["validation"]["issues"] if i["severity"] == "ERROR"]
    assert detail["error"] == errors[0][0] and detail["message"].startswith("Validation errors block")
    assert set(expected) <= set(errors), errors
    assert detail["validation"]["ok"] is False
    assert _state(did) == before and db_fingerprint() == fingerprint
    return detail


# ------------------------------------------------------------------------------------------------ invalid content
def _nested_unmapped(tag):
    return branch("ALL", f"{tag}_root", [
        branch("ANY", f"{tag}_ok", [pred(f"{tag}_p1", flag=True), pred(f"{tag}_p2")], [f"sm_{tag}_a"]),
        branch("ANY", f"{tag}_bad", [pred(f"{tag}_p3", flag=True), pred(f"{tag}_p4", fact="entity.financial_entity_type")])])


SHAPES = {  # name -> (draft is decision-table view, replacement content, expected (code, node_id) errors)
    "flag_without_mapping": (False, lambda t: {"ast": two_preds(t, flag=True)}, lambda t: [(REQUIRED, f"{t}_a"), (REQUIRED, f"{t}_b")]),
    "ast_table_row_without_mapping": (False, lambda t: {"ast": table_ast(t, root_mapping=[f"sm_{t}_root"], row_mapping=None)},
                                      lambda t: [(REQUIRED, f"row_{t}_R1")]),
    "nested_unmapped_branch": (False, lambda t: {"ast": _nested_unmapped(t)}, lambda t: [(REQUIRED, f"{t}_p3"), (REQUIRED, f"{t}_p4")]),
    "malformed_reference_string": (False, lambda t: {"ast": two_preds(t, root_mapping=f"sm_{t}_root", flag=True)},
                                   lambda t: [(SCHEMA, f"{t}_root"), (REQUIRED, f"{t}_a"), (REQUIRED, f"{t}_b")]),
    "malformed_reference_blank": (False, lambda t: {"ast": two_preds(t, root_mapping=[" "])},
                                  lambda t: [(SCHEMA, f"{t}_root"), (REQUIRED, f"{t}_a"), (REQUIRED, f"{t}_b")]),
    "decision_table_row_without_mapping": (True, lambda t: {"decision_table": table(t, root_mapping=[f"sm_{t}_root"], row_mapping=None)},
                                           lambda t: [(REQUIRED, "row_R1")]),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("stage", ["peer-approve", "legal-approve"])
def test_invalid_current_content_cannot_pass_an_approval(client, world, stage, shape):
    """The draft was valid when submitted (and, for legal approval, when peer-approved); its content is now invalid.
    The approval revalidates what is stored now, refuses, and writes nothing."""
    dt, replacement, expected = SHAPES[shape]
    tag = f"s{next(_n)}"
    did = _submitted(client, tag, decision_table=dt)
    if stage == "legal-approve":
        assert client.post(f"/api/rules/drafts/{did}/peer-approve", headers=H(PEER)).status_code == 200
    _replace_content(did, **replacement(tag))
    assert _draft_row(did)["validation_report"]["ok"] is True  # the stale stored report still says ok
    _refused(client, did, stage, PEER if stage == "peer-approve" else LEGAL, expected(tag))
    row = _draft_row(did)
    assert row["status"] == "IN_REVIEW" and row["legal_reviewer_id"] is None
    assert row["peer_reviewer_id"] == (None if stage == "peer-approve" else PEER)


def test_draft_in_review_before_the_fix_cannot_advance(client, world):
    """A draft whose inherits_mapping bypass the old validator accepted: IN_REVIEW with an ok report, then also
    peer-approved under the old code. Neither approval accepts it now."""
    tag = f"old{next(_n)}"
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=_body(tag, ast=two_preds(tag, flag=True)))
    did = r.json()["draft_id"]
    _replace_content(did, status="IN_REVIEW", validation_report={"ok": True, "issues": [], "error_count": 0})
    _refused(client, did, "peer-approve", PEER, [(REQUIRED, f"{tag}_a"), (REQUIRED, f"{tag}_b")])
    _replace_content(did, peer_reviewer_id=PEER)
    _refused(client, did, "legal-approve", LEGAL, [(REQUIRED, f"{tag}_a"), (REQUIRED, f"{tag}_b")])
    assert _draft_row(did)["status"] == "IN_REVIEW"


def test_draft_modified_after_a_successful_validation_is_revalidated(client, world):
    """Through the API an edit resets the review (status DRAFT) and the new content must pass submit-review again; below
    the API, content changed while IN_REVIEW is caught by the approval's own validation."""
    tag = f"mod{next(_n)}"
    did = _submitted(client, tag)
    assert client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()["ok"] is True
    assert client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=_body(tag, ast=two_preds(tag, flag=True))).status_code == 200
    r = client.post(f"/api/rules/drafts/{did}/peer-approve", headers=H(PEER))
    assert r.status_code == 409 and r.json()["detail"] == "Draft not in review"
    assert client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR)).status_code == 409
    _replace_content(did, status="IN_REVIEW", ast=two_preds(tag, flag=True))
    _refused(client, did, "peer-approve", PEER, [(REQUIRED, f"{tag}_a")])
    _replace_content(did, ast=_valid_ast(tag))  # corrected content is accepted on its own merits
    assert client.post(f"/api/rules/drafts/{did}/peer-approve", headers=H(PEER)).status_code == 200


# ------------------------------------------------------------------------------------------------ valid content
@pytest.mark.parametrize("case", ["inherit_without_flag", "inherit_with_flag", "nested", "decision_table"])
def test_valid_drafts_pass_both_approvals_and_publish(client, world, case):
    tag = f"ok{next(_n)}"
    if case == "decision_table":
        body = _body(tag, decision_table=_valid_table(tag))
    elif case == "nested":
        body = _body(tag, ast=branch("ALL", f"{tag}_root", [
            branch("ANY", f"{tag}_mid", [branch("ALL", f"{tag}_in", [pred(f"{tag}_a", flag=True), pred(f"{tag}_a2")]),
                                         pred(f"{tag}_a3", flag=True)], [f"sm_{tag}_a"]),
            pred(f"{tag}_b", mapping=[f"sm_{tag}_b"], fact="entity.financial_entity_type")]))
    else:
        body = _body(tag, ast=two_preds(tag, root_mapping=[f"sm_{tag}_root"], flag=True if case == "inherit_with_flag" else None))
    did = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body).json()["draft_id"]
    assert client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR)).status_code == 200
    before = _state(did)
    r = client.post(f"/api/rules/drafts/{did}/peer-approve", headers=H(PEER))
    assert r.status_code == 200 and r.json()["peer_reviewer_id"] == PEER and r.json()["status"] == "IN_REVIEW"
    r = client.post(f"/api/rules/drafts/{did}/legal-approve", headers=H(LEGAL))
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED" and r.json()["legal_reviewer_id"] == LEGAL
    after = _state(did)
    assert after["audit"] > before["audit"] and after["outbox"] > before["outbox"]  # rule.legal_approve + rule.verified
    assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 200
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"rar-{did[:8]}", "changelog": "ok"})
    assert r.status_code == 200, r.text


def test_seeded_rule_drafts_still_pass_approval_validation(client, world):
    """The approval validation accepts the content of every seeded, published rule (legitimate inheritance included)."""
    from types import SimpleNamespace
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, RuleDraft, RuleVersion
    from app.routers.rules import _validate
    seeded = ("EU-2024-1689", "EU-2022-2554", "UK-GDPR", "EU-2022-2555", "FCA-PRIN-2A")
    with AdminSession() as db:
        drafts = db.execute(select(RuleDraft).join(RuleVersion, RuleVersion.draft_id == RuleDraft.draft_id)
                            .join(ObligationDefinition, ObligationDefinition.obligation_definition_id == RuleVersion.obligation_definition_id)
                            .join(Instrument, Instrument.instrument_id == ObligationDefinition.instrument_id)
                            .where(Instrument.identifier.in_(seeded))).scalars().all()
        reports = [(d.draft_id, d.authoring_mode, _validate(SimpleNamespace(db=db), d)) for d in drafts]
    assert reports and {m for _, m, _ in reports} == {"AST", "DECISION_TABLE"}
    assert [(did, rep["issues"]) for did, _, rep in reports if not rep["ok"]] == []


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL)
def _approve_in_thread(stage, did, out):
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.routers.rules import legal_approve, peer_approve
    from app.security import Ctx
    actor = PEER if stage == "peer-approve" else LEGAL
    with SessionLocal() as db:
        set_principal(db, None, actor)
        db.execute(text("SET lock_timeout = '30s'"))
        try:
            (peer_approve if stage == "peer-approve" else legal_approve)(did, Ctx(db=db, actor=db.get(Actor, actor)))
            out["status"] = 200
        except HTTPException as e:
            out["status"], out["detail"] = e.status_code, e.detail
            db.rollback()


@pytest.mark.parametrize("stage", ["peer-approve", "legal-approve"])
def test_a_concurrent_edit_is_validated_before_approval_not_after(client, world, stage):
    """An uncommitted edit makes the draft's content invalid (status untouched). The approval waits for the draft row,
    then validates the committed new content and refuses. Without the row lock it would validate the old content,
    then record its approval against the new one."""
    from app.db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.models import RuleDraft
    tag = f"race{next(_n)}"
    did = _submitted(client, tag)
    if stage == "legal-approve":
        assert client.post(f"/api/rules/drafts/{did}/peer-approve", headers=H(PEER)).status_code == 200
    out: dict = {}
    with admin_engine.connect() as c:
        tx = c.begin()
        c.execute(update(RuleDraft.__table__).where(RuleDraft.__table__.c.draft_id == did).values(ast=two_preds(tag, flag=True)))
        worker = threading.Thread(target=_approve_in_thread, args=(stage, did, out))
        worker.start()
        worker.join(1.5)
        assert worker.is_alive() and not out, "the approval must wait for the draft row while the edit is uncommitted"
        tx.commit()
    worker.join(30)
    assert not worker.is_alive()
    assert out["status"] == 409 and out["detail"]["error"] == REQUIRED, out
    row = _draft_row(did)
    assert row["status"] == "IN_REVIEW" and row["legal_reviewer_id"] is None
    assert row["peer_reviewer_id"] == (None if stage == "peer-approve" else PEER)


def test_an_edit_during_an_approval_waits_and_then_invalidates_it(client, world):
    """The approval holds the draft row from its validation to its commit. An edit arriving in between waits, then
    resets the review, so no approval survives for content other than the content validated."""
    from app.db import IS_POSTGRES, SessionLocal, set_principal
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.models import Actor
    from app.routers.rules import _locked_draft, _require_valid_for_approval
    from app.security import Ctx
    tag = f"race{next(_n)}"
    did = _submitted(client, tag)
    edit: dict = {}

    def put():
        edit["response"] = client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=_body(tag, ast=two_preds(tag, flag=True)))
    with SessionLocal() as db:
        set_principal(db, None, PEER)
        ctx = Ctx(db=db, actor=db.get(Actor, PEER))
        d = _locked_draft(ctx, did)
        _require_valid_for_approval(ctx, d, "peer approval")
        worker = threading.Thread(target=put)
        worker.start()
        worker.join(1.5)
        assert worker.is_alive() and not edit, "the edit must wait for the approval's draft row lock"
        d.peer_reviewer_id = PEER
        db.commit()
    worker.join(30)
    assert not worker.is_alive() and edit["response"].status_code == 200, edit
    row = _draft_row(did)
    assert row["status"] == "DRAFT" and row["peer_reviewer_id"] is None and row["ast"] == two_preds(tag, flag=True)
