"""Rule supersession and determination adoption: only the behaviour the controlled specifications define.

  * ZA-APP-001 §10 "RuleVersion published/corrected -> New evaluation; adopted determination flagged for review if outcome
    materially differs"; "no silent rewrite of previous result".
  * ZA-APP-001 §05 "Optionally close/supersede a still-PROPOSED candidate created by an earlier evaluation; adopted
    historical determinations are never silently removed. Raise review if a new evaluation conflicts with current adopted
    determination." §09 "Supersession: New determination revision ... Prior history remains."
  * ZA-APP-001 §06 "an attestor cannot adopt a determination whose backing evaluation has been superseded without explicit
    confirmation/re-evaluation policy"; §16 "stale/superseded revision cannot be silently attested".
  * ZA-RUL-001 §15 / impact: a subject that could not be re-evaluated makes the impact INCOMPLETE, never "no impact".
  * ZA-TIM-001: historical evaluations are immutable and replay.

Not tested, because no specification defines it (an APP-owner decision): adopting a determination whose evaluation ran a
rule version that is no longer in force when no newer evaluation exists (re-evaluation failed, or none was run). Runs on
SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RSA-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-rsa-{TAG.lower()}", f"rsa-cco-{TAG.lower()}"
EU, KIND, MICRO = "entity.established_in_eu", "entity.financial_entity_type", "entity.microenterprise"
BASIS = "Board delegation RSA"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _ast(t: str, kind: str, micro: bool = False) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{t}_{nid}", "operator": "EQ", "source_mappings": [f"sm_{t}_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{t}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{t}_{nid}c", "type": typ, "value": value}]}
    children = [pred("eu", EU, "BOOLEAN", True), pred("kind", KIND, "ENUM", kind)]
    if micro:
        children.append(pred("micro", MICRO, "BOOLEAN", False))
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": children}


def _vectors(kind: str, micro: bool) -> list[dict]:
    def vec(vid, cls, facts, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": facts, "expected_outcome": out}
    base = {EU: True, KIND: kind, **({MICRO: False} if micro else {})}
    return [vec("P", "POSITIVE", base, "APPLIES"), vec("N", "NEGATIVE", {**base, EU: False}, "DOES_NOT_APPLY"),
            vec("U", "UNKNOWN", {**base, KIND: {"unknown": "MISSING_REQUIRED_FACT"}}, "UNCERTAIN")]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Supersession Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="RSA CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Supersession Act",
        "locator": f"OJ L {TAG}/rsa", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RSA-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code == 201, rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return True


def _definition() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RSA_{TAG}_{secrets.token_hex(2)}", title="Supersession obligation",
                                 instrument_id=inst.instrument_id, provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _publish(odef, kind="CREDIT_INSTITUTION", micro=False, change_class="EXECUTABLE_OUTCOME", reason=None) -> dict:
    client, t, p = S["client"], secrets.token_hex(3), S["rv"]["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": S["rv"]["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    mappings = [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")] + ([m(f"sm_{t}_micro")] if micro else [])
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(t, kind, micro),
        "golden_vectors": _vectors(kind, micro), "valid_from": "2025-01-01", "change_class": change_class,
        "source_mappings": mappings})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={
        "label": f"rsa-{secrets.token_hex(4)}", "changelog": "rsa", **({"correction_reason": reason} if reason else {})})
    assert r.status_code == 200, r.text
    return r.json()


def _proposed(name, odef) -> dict:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    for key, value in ((EU, True), (KIND, "CREDIT_INSTITUTION")):
        r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=40)).isoformat()})
        assert r.status_code == 200, r.text
    ev = S["client"].post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": odef})
    assert ev.status_code == 200 and ev.json()["current_determination"]["state"] == "PROPOSED", ev.text
    return {"sid": sid, "ev": ev.json(), "lineage": ev.json()["current_determination"]["lineage_id"]}


def _attest(lineage, **extra):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO),
                            json={"decision": "ADOPT", "authority_basis": BASIS, **extra})


def _current(lineage):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        return db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.lineage_id == lineage)
                          .order_by(ApplicabilityDetermination.record_from_seq)).scalars().all()


def _evaluations(sid):
    return S["client"].get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": sid}).json()


def _replay(evaluation_id):
    return S["client"].get(f"/api/applicability/evaluations/{evaluation_id}", headers=H(CCO)).json()["replay"]["status"]


def _obligations(sid):
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        return db.execute(select(ObligationInstance).where(ObligationInstance.subject_id == sid)
                          .order_by(ObligationInstance.record_from_seq)).scalars().all()


# ------------------------------------------------------------------------------------------------ proposals
def test_a_proposal_moves_to_the_superseding_rules_evaluation_and_its_history_is_kept(client, world):
    """APP §05/§10: the publication re-evaluates the subject; the still-PROPOSED determination is superseded by a new
    revision on the new evaluation (here DOES_NOT_APPLY); the earlier revision and evaluation remain and replay."""
    odef = _definition()
    rv1 = _publish(odef)["rule_version"]["rule_version_id"]
    x = _proposed("proposal", odef)
    rv2 = _publish(odef, kind="PAYMENT_INSTITUTION")["rule_version"]["rule_version_id"]
    revs = _current(x["lineage"])
    assert [(r.state, r.outcome, r.record_to_seq is None) for r in revs] == [("PROPOSED", "APPLIES", False),
                                                                             ("PROPOSED", "DOES_NOT_APPLY", True)]
    evs = {e["evaluation_id"]: e for e in _evaluations(x["sid"])}
    assert evs[revs[0].evaluation_id]["rule_version_id"] == rv1 and evs[revs[1].evaluation_id]["rule_version_id"] == rv2
    assert evs[revs[1].evaluation_id]["trigger"] == "RULE_CHANGED"
    assert {_replay(e) for e in evs} == {"REPRODUCED"}


def test_a_review_made_before_supersession_cannot_be_attested_silently(client, world):
    """APP §16: the revision and the evaluation the attestor reviewed before the new rule are refused, and nothing is
    written; adopting the current revision rests on the superseding rule's evaluation."""
    odef = _definition()
    _publish(odef)
    x = _proposed("stale-review", odef)
    reviewed = _current(x["lineage"])[0]
    rv2 = _publish(odef)["rule_version"]["rule_version_id"]  # same outcome: still a new evaluation and revision
    before = db_fingerprint()
    stale = [_attest(x["lineage"], revision_id=reviewed.revision_id), _attest(x["lineage"], evaluation_id=reviewed.evaluation_id)]
    assert [(r.status_code, r.json()["detail"]["error"]) for r in stale] == [(409, "STALE_DETERMINATION_REVISION"),
                                                                             (409, "STALE_EVALUATION")]
    assert db_fingerprint() == before and _obligations(x["sid"]) == []
    r = _attest(x["lineage"])
    assert r.status_code == 200, r.text
    ev = client.get(f"/api/applicability/evaluations/{r.json()['determination']['evaluation_id']}", headers=H(CCO)).json()
    assert ev["rule_version_id"] == rv2 and ev["evaluation_id"] != reviewed.evaluation_id


def test_an_uncertain_re_evaluation_blocks_adoption_of_the_older_evaluation(client, world):
    """APP §06: the new rule needs a fact the subject lacks, so its evaluation is UNCERTAIN (fact requests raised) and does
    not replace the proposal; the proposal's backing evaluation is no longer the latest, so it cannot be adopted."""
    odef = _definition()
    _publish(odef)
    x = _proposed("uncertain", odef)
    _publish(odef, micro=True)
    latest = _evaluations(x["sid"])[0]
    assert latest["outcome"] == "UNCERTAIN" and [r.evaluation_id for r in _current(x["lineage"])] == [x["ev"]["evaluation_id"]]
    before = db_fingerprint()
    r = _attest(x["lineage"])
    assert r.status_code == 409 and r.json()["detail"] == "A newer evaluation exists; re-review before attesting", r.text
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ adopted determinations
def test_an_adopted_determination_is_flagged_only_when_the_new_outcome_differs_and_is_never_removed(client, world):
    odef = _definition()
    rv1 = _publish(odef)["rule_version"]["rule_version_id"]
    x = _proposed("adopted", odef)
    adopted = _attest(x["lineage"]).json()
    same = _publish(odef)  # same outcome: a new evaluation, no review
    [cur] = [r for r in _current(x["lineage"]) if r.record_to_seq is None]
    assert (cur.state, cur.needs_review, cur.revision_id) == ("ADOPTED", False, adopted["determination"]["revision_id"])
    assert _evaluations(x["sid"])[0]["rule_version_id"] == same["rule_version"]["rule_version_id"]
    _publish(odef, kind="PAYMENT_INSTITUTION")  # a different outcome: flagged for review
    [cur] = [r for r in _current(x["lineage"]) if r.record_to_seq is None]
    assert (cur.state, cur.outcome, cur.needs_review) == ("ADOPTED", "APPLIES", True)
    # Never silently removed: the obligation and the adoption it rests on are unchanged until a new decision.
    [o] = _obligations(x["sid"])
    assert (o.lifecycle, o.record_to_seq, o.determination_revision_id) == ("ADOPTED", None, adopted["determination"]["revision_id"])
    ev = client.get(f"/api/applicability/evaluations/{adopted['determination']['evaluation_id']}", headers=H(CCO)).json()
    assert (ev["rule_version_id"], ev["replay"]["status"]) == (rv1, "REPRODUCED")


# ------------------------------------------------------------------------------------------------ failed re-evaluation
def test_a_failed_re_evaluation_leaves_the_decision_unchanged_and_the_impact_incomplete(client, world, monkeypatch):
    """RUL §15: a subject the publication could not re-evaluate is reported, never 'no impact'; APP §10: nothing is
    silently rewritten, so the proposal stays on its evaluation and no new evaluation exists. (What may then be adopted is
    the undefined case this module does not test.)"""
    from app.engines import applicability as appl
    odef = _definition()
    _publish(odef)
    x = _proposed("failed", odef)
    real = appl.evaluate

    def failing(db, **kw):
        if kw.get("trigger") == "RULE_CHANGED" and kw["subject"].subject_id == x["sid"]:
            raise HTTPException(503, {"error": "EVALUATOR_UNAVAILABLE", "message": "simulated"})
        return real(db, **kw)
    monkeypatch.setattr(appl, "evaluate", failing)
    pub = _publish(odef, kind="PAYMENT_INSTITUTION")
    monkeypatch.undo()
    assert pub["impact"]["status"] == "INCOMPLETE" and pub["impact"]["failed"].get("EVALUATOR_UNAVAILABLE", 0) >= 1, pub["impact"]
    assert [e["evaluation_id"] for e in _evaluations(x["sid"])] == [x["ev"]["evaluation_id"]]
    assert [(r.state, r.evaluation_id) for r in _current(x["lineage"])] == [("PROPOSED", x["ev"]["evaluation_id"])]
    assert _replay(x["ev"]["evaluation_id"]) == "REPRODUCED"


# ------------------------------------------------------------------------------------------------ correction
def test_a_correction_release_re_evaluates_and_flags_with_a_notice_and_history(client, world):
    """APP §10 "Content correction -> Correction notice + new evaluation; prior knowledge-time history preserved"."""
    odef = _definition()
    first = _publish(odef)
    x = _proposed("corrected", odef)
    adopted = _attest(x["lineage"]).json()
    corr = _publish(odef, kind="PAYMENT_INSTITUTION", change_class="CORRECTION", reason="Wrong entity type")
    assert corr["content_release"]["correction_of_release_id"] == first["content_release"]["content_release_id"]
    [cur] = [r for r in _current(x["lineage"]) if r.record_to_seq is None]
    assert cur.needs_review and cur.revision_id != adopted["determination"]["revision_id"]
    d = client.get(f"/api/obligations/{adopted['obligation']['lineage_id']}", headers=H(CCO)).json()
    assert d["regulatory_provenance"]["content_release"]["corrected_by_release_ids"] == [corr["content_release"]["content_release_id"]]
    assert d["regulatory_provenance"]["rule_version_id"] == first["rule_version"]["rule_version_id"]
    assert {_replay(e["evaluation_id"]) for e in _evaluations(x["sid"])} == {"REPRODUCED"}
