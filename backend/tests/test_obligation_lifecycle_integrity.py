"""Obligation lifecycle transitions and concurrency integrity (determination adoption, rejection, challenge, re-review,
supersession, closure and re-adoption).

Controlling text:
  * ZA-GLS-001 §05 "Determination workflow": PROPOSED "May move to IN_REVIEW or ADOPTED where attestation policy
    permits"; IN_REVIEW "May move to ADOPTED, REJECTED or SUPERSEDED"; ADOPTED "Creates/activates obligation when
    outcome = APPLIES"; REJECTED "New facts/rule version may create a new proposal".
  * ZA-APP-001 §09 (stages, challenge types), §11 C steps 4-6 (a differing newer evaluation flags the adopted
    determination; a new decision is collected as an AD revision / attestation; the obligation is revised, history kept),
    §06 atomic transactions, §12 attest "exact determination revision" and minimum event payloads, §14 audit of every
    human adoption/challenge, §15 one current adopted/rejected determination per subject + definition, §16 "stale/
    superseded revision cannot be silently attested"; APP-REQ-009/011/012/016.
  * ZA-EVT-001: determination.rejected, obligation.revised.

Each scenario uses its own subject, so each has its own determination lineage. Concurrency tests run on PostgreSQL 17
only: one thread is held inside the critical section while the other is shown to wait, so the interleaving is
deterministic rather than left to timing.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"OLI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-olia-{TAG.lower()}", f"tenant-olib-{TAG.lower()}"
CCO, CCO_B = f"oli-cco-{TAG.lower()}", f"olib-cco-{TAG.lower()}"
ANALYST, AUDITOR, AGENT = f"oli-analyst-{TAG.lower()}", f"oli-audit-{TAG.lower()}", f"oli-agent-{TAG.lower()}"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


# ------------------------------------------------------------------------------------------------ world
def _ast(entity_type: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": "oli_root", "source_mappings": ["sm_oli_root"], "children": [
        pred("oli_1", "entity.established_in_eu", "BOOLEAN", True),
        pred("oli_2", "entity.financial_entity_type", "ENUM", entity_type)]}


def _vectors(t: str) -> list[dict]:
    facts = lambda eu, v: {"entity.established_in_eu": eu, "entity.financial_entity_type": v}  # noqa: E731
    return [{"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
             "facts": facts(True, t), "expected_outcome": "APPLIES"},
            {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
             "facts": facts(False, t), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
             "facts": facts(True, {"unknown": "MISSING_REQUIRED_FACT"}), "expected_outcome": "UNCERTAIN"}]


def _subject(name: str) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"  # ID columns are 36 characters
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    return sid


def _fact(client, sid, key, value, *, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=40 if not change else 1)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "Entity type changed"}
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _applies_subject(client, name: str, *, evaluate=True) -> dict:
    sid = _subject(name)
    _fact(client, sid, "entity.established_in_eu", True)
    _fact(client, sid, "entity.financial_entity_type", "CREDIT_INSTITUTION")
    out = {"sid": sid}
    if evaluate:
        ev = _evaluate(client, sid)
        assert ev["outcome"] == "APPLIES" and ev["current_determination"]["state"] == "PROPOSED"
        out |= {"ev": ev, "lineage": ev["current_determination"]["lineage_id"]}
    return out


def _evaluate(client, sid, actor=CCO):
    r = client.post("/api/applicability/evaluations", headers=H(actor),
                    json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert r.status_code == 200, r.text
    return r.json()


def _attest(client, lineage, decision="ADOPT", actor=CCO, **extra):
    return client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(actor),
                       json={"decision": decision, "authority_basis": "Board delegation OLI", **extra})


def _challenge(client, lineage, cls="FACT_WRONG", actor=CCO, detail="The entity type is wrong"):
    return client.post(f"/api/applicability/determinations/{lineage}/challenge", headers=H(actor),
                       json={"challenge_class": cls, "detail": detail})


def _revisions(lineage):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        return db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.lineage_id == lineage)
                          .order_by(ApplicabilityDetermination.record_from_seq)).scalars().all()


def _current(lineage):
    return [r for r in _revisions(lineage) if r.record_to_seq is None]


def _obligations(sid):
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        return db.execute(select(ObligationInstance).where(ObligationInstance.subject_id == sid)
                          .order_by(ObligationInstance.record_from_seq)).scalars().all()


def _events(aggregate_id, event_type=None):
    from app.db import admin_engine
    q = "SELECT event_type, payload FROM platform_outbox_event WHERE aggregate_id = :a"
    with admin_engine.connect() as c:
        rows = c.execute(text(q + " ORDER BY record_seq, event_id"), {"a": aggregate_id}).all()
    import json
    out = [(t, p if isinstance(p, dict) else json.loads(p)) for t, p in rows]
    return [x for x in out if event_type is None or x[0] == event_type]


def _audits(object_id, action):
    from app.db import admin_engine
    with admin_engine.connect() as c:
        return c.execute(text("SELECT prior_state, new_state, reason FROM platform_audit_event "
                              "WHERE object_id = :o AND action = :a ORDER BY record_seq"), {"o": object_id, "a": action}).all()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import ControlDefinition, Instrument, ObligationDefinition, Tenant

    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Lifecycle Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=f"CTRL-{TAG}-OLI", title="Lifecycle control", objective="Probe.",
                                 category="RISK_MANAGEMENT"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="OLI CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="OLI CCO B", roles=["CCO"])
        add_principal(db, actor_id=ANALYST, tenant_id=TA, actor_type="HUMAN", display_name="OLI analyst",
                      roles=["REGULATORY_ANALYST"])
        add_principal(db, actor_id=AUDITOR, tenant_id=TA, actor_type="HUMAN", display_name="OLI audit",
                      roles=["INTERNAL_AUDIT"])
        add_principal(db, actor_id=AGENT, tenant_id=TA, actor_type="AGENT", display_name="OLI agent", roles=["CCO"])
        db.commit()

    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Lifecycle Act",
        "locator": f"OJ L {TAG}/oli", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"OLI-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"OLI_{TAG}", title="Lifecycle obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True,
                                 satisfaction_template={"node_type": "CONTROL", "node_id": "c", "control_key": f"CTRL-{TAG}-OLI"})
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    p0 = rv["provisions"][0]
    m = lambda sid: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],  # noqa: E731
                     "provision_id": p0["provision_id"], "authoritative_reference": p0["locator"],
                     "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast("CREDIT_INSTITUTION"),
        "golden_vectors": _vectors("CREDIT_INSTITUTION"), "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m("sm_oli_root"), m("sm_oli_1"), m("sm_oli_2")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"oli-{TAG}", "changelog": "oli"})
    assert r.status_code == 200, r.text
    return True


# ------------------------------------------------------------------------------------------------ GLS transitions
def test_proposed_cannot_be_rejected_directly_and_nothing_is_written(client, world):
    x = _applies_subject(client, "reject-proposed")
    before = db_fingerprint()
    r = _attest(client, x["lineage"], "REJECT", reason="Not for us")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "DETERMINATION_TRANSITION_NOT_PERMITTED"
    assert r.json()["detail"]["from_state"] == "PROPOSED"
    assert db_fingerprint() == before


def test_challenge_moves_proposed_to_in_review_with_audit_and_event(client, world):
    x = _applies_subject(client, "challenge")
    S["in_review"] = x
    r = _challenge(client, x["lineage"])
    assert r.status_code == 200, r.text
    ch = r.json()
    cur = _current(x["lineage"])
    assert [(c.state, c.evaluation_id) for c in cur] == [("IN_REVIEW", x["ev"]["evaluation_id"])]
    assert _audits(x["lineage"], "determination.challenge")[-1][:2] == ("PROPOSED", "IN_REVIEW")
    [(_, payload)] = _events(x["lineage"], "determination.challenged")
    assert payload["challenge_id"] == ch["challenge_id"] and payload["evaluation_id"] == x["ev"]["evaluation_id"]
    assert payload["challenge_class"] == "FACT_WRONG" and payload["determination_revision_id"]
    # The evaluation the challenge names is unchanged (APP-REQ-011).
    assert client.get(f"/api/applicability/evaluations/{x['ev']['evaluation_id']}", headers=H(CCO)).json()["outcome"] == "APPLIES"


@pytest.mark.parametrize("body, code", [({"challenge_class": "NOT_A_CLASS", "detail": "x"}, "CHALLENGE_CLASS_INVALID"),
                                        ({"challenge_class": "FACT_WRONG", "detail": "   "}, "CHALLENGE_DETAIL_REQUIRED")])
def test_invalid_challenge_is_refused_without_writes(client, world, body, code):
    x = S.get("in_review") or _applies_subject(client, "challenge-bad")
    before = db_fingerprint()
    r = client.post(f"/api/applicability/determinations/{x['lineage']}/challenge", headers=H(CCO), json=body)
    assert r.status_code == 422 and r.json()["detail"]["error"] == code
    assert db_fingerprint() == before


def test_in_review_rejection_requires_a_reason_and_records_rejected(client, world):
    x = S["in_review"]
    for blank in (None, "", "   "):
        before = db_fingerprint()
        r = _attest(client, x["lineage"], "REJECT", **({"reason": blank} if blank is not None else {}))
        assert r.status_code == 422, r.text
        assert db_fingerprint() == before
    r = _attest(client, x["lineage"], "REJECT", reason="  Declined by the board  ")
    assert r.status_code == 200, r.text
    det, att = r.json()["determination"], r.json()["attestation"]
    assert det["state"] == "REJECTED" and det["basis"] == "Declined by the board" and att["decision"] == "DECLINED"
    assert r.json()["obligation"] is None and _obligations(x["sid"]) == []
    [(_, payload)] = _events(x["lineage"], "determination.rejected")
    assert payload["attestation_id"] == att["attestation_id"] and payload["evaluation_id"] == x["ev"]["evaluation_id"]
    # REJECTED cannot be attested again; "new facts/rule version may create a new proposal" (GLS).
    assert _attest(client, x["lineage"]).status_code == 409
    _fact(client, x["sid"], "entity.established_in_eu", True, change=True)
    cur = _current(x["lineage"])
    assert [c.state for c in cur] == ["PROPOSED"] and cur[0].evaluation_id != x["ev"]["evaluation_id"]
    assert _attest(client, x["lineage"]).status_code == 200
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED"]


def test_authority_basis_is_required(client, world):
    x = _applies_subject(client, "no-authority")
    before = db_fingerprint()
    r = client.post(f"/api/applicability/determinations/{x['lineage']}/attest", headers=H(CCO),
                    json={"decision": "ADOPT", "authority_basis": "   "})
    assert r.status_code == 422
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ adoption, re-review, closure
def test_adoption_activates_one_obligation_with_complete_event_and_audit(client, world):
    x = _applies_subject(client, "adopt")
    S["adopt"] = x
    rev_seen = _current(x["lineage"])[0].revision_id
    r = _attest(client, x["lineage"], revision_id=rev_seen, evaluation_id=x["ev"]["evaluation_id"])
    assert r.status_code == 200, r.text
    S["adopted"] = r.json()
    obl = r.json()["obligation"]
    assert obl["lifecycle"] == "ADOPTED" and obl["determination_revision_id"] == r.json()["determination"]["revision_id"]
    [(_, payload)] = _events(x["lineage"], "determination.adopted")
    assert payload["obligation_lineage_id"] == obl["lineage_id"] and payload["obligation_revision_id"] == obl["revision_id"]
    assert payload["evaluation_id"] == x["ev"]["evaluation_id"]
    assert _audits(x["lineage"], "determination.attest")[-1][:2] == ("PROPOSED", "ADOPTED")
    # An ADOPTED determination that is not flagged cannot be attested again; a repeat is refused and writes nothing.
    before = db_fingerprint()
    assert _attest(client, x["lineage"]).status_code == 409
    assert db_fingerprint() == before
    # A challenge of an adopted determination is recorded without changing its state (APP-REQ-011).
    assert _challenge(client, x["lineage"], "INTERPRETATION").status_code == 200
    assert [c.state for c in _current(x["lineage"])] == ["ADOPTED"]


def test_review_of_flagged_adoption_closes_then_readopts_with_full_history(client, world):
    """APP §11 C: a differing newer evaluation flags the adopted determination; the new decision is an AD revision on
    that evaluation; the obligation is revised (CLOSED) with history kept; a later APPLIES re-adopts the same lineage."""
    x = S["adopt"]
    lineage, obl_lineage = x["lineage"], S["adopted"]["obligation"]["lineage_id"]
    seq_adopted = S["adopted"]["obligation"]["record_from_seq"]
    stale_rev = _current(lineage)[0].revision_id
    _fact(client, x["sid"], "entity.financial_entity_type", "PAYMENT_INSTITUTION", change=True)
    [flagged] = _current(lineage)
    assert flagged.state == "ADOPTED" and flagged.needs_review and flagged.outcome == "APPLIES"
    e2 = client.get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": x["sid"]}).json()[0]
    assert e2["outcome"] == "DOES_NOT_APPLY"

    before = db_fingerprint()
    r = _attest(client, lineage, "REJECT", reason="Disagree")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "DETERMINATION_TRANSITION_NOT_PERMITTED"
    r = _attest(client, lineage, revision_id=stale_rev)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "STALE_DETERMINATION_REVISION"
    r = _attest(client, lineage, revision_id=flagged.revision_id, evaluation_id=x["ev"]["evaluation_id"])
    assert r.status_code == 409 and r.json()["detail"] == {**r.json()["detail"], "error": "STALE_EVALUATION",
                                                            "evaluation_id": e2["evaluation_id"]}
    assert db_fingerprint() == before

    r = _attest(client, lineage, revision_id=flagged.revision_id, evaluation_id=e2["evaluation_id"])
    assert r.status_code == 200, r.text
    close = r.json()
    assert close["determination"]["outcome"] == "DOES_NOT_APPLY" and close["determination"]["state"] == "ADOPTED"
    assert close["determination"]["evaluation_id"] == e2["evaluation_id"]
    revs = _obligations(x["sid"])
    assert [o.lifecycle for o in revs] == ["ADOPTED", "CLOSED"] and len({o.lineage_id for o in revs}) == 1
    [(_, revised)] = _events(obl_lineage, "obligation.revised")
    assert revised["lifecycle"] == "CLOSED" and revised["closing_attestation_id"] == close["attestation"]["attestation_id"]
    assert revised["closing_determination_revision_id"] == close["determination"]["revision_id"]
    adopted_events = _events(lineage, "determination.adopted")
    assert adopted_events[-1][1]["obligation_revision_id"] == revs[1].revision_id

    # Re-adoption: the entity type changes back; the new APPLIES evaluation flags the adopted DOES_NOT_APPLY decision.
    _fact(client, x["sid"], "entity.financial_entity_type", "CREDIT_INSTITUTION", change=True)
    [flag2] = _current(lineage)
    assert flag2.needs_review and flag2.outcome == "DOES_NOT_APPLY"
    r = _attest(client, lineage, revision_id=flag2.revision_id)
    assert r.status_code == 200, r.text
    revs = _obligations(x["sid"])
    assert [o.lifecycle for o in revs] == ["ADOPTED", "CLOSED", "ADOPTED"] and len({o.lineage_id for o in revs}) == 1
    assert revs[2].valid_from >= revs[1].valid_to  # never re-claims the closed interval (F-06)

    # Every decision explains itself, and the history as known at the first adoption is unchanged.
    hist = client.get(f"/api/obligations/{obl_lineage}", headers=H(CCO)).json()["decision_history"]
    assert hist["integrity"]["status"] == "INTACT", hist["integrity"]
    assert [h["lifecycle"] for h in hist["revisions"]] == ["ADOPTED", "CLOSED", "ADOPTED"]
    assert hist["revisions"][1]["closure"]["evaluation"]["evaluation_id"] == e2["evaluation_id"]
    old = client.get(f"/api/obligations/{obl_lineage}", params={"as_of_record_seq": seq_adopted}, headers=H(CCO)).json()
    again = client.get(f"/api/obligations/{obl_lineage}", params={"as_of_record_seq": seq_adopted}, headers=H(CCO)).json()
    assert old["decision_history"] == again["decision_history"]
    assert [h["lifecycle"] for h in old["decision_history"]["revisions"]] == ["ADOPTED"]
    assert old["obligation"]["determination_revision_id"] == S["adopted"]["determination"]["revision_id"]
    # Every determination revision of the lineage is retained, each with its own evaluation.
    states = [(r.state, r.outcome, r.needs_review) for r in _revisions(lineage)]
    assert states[0] == ("PROPOSED", "APPLIES", False) and states[-1] == ("ADOPTED", "APPLIES", False)
    assert ("ADOPTED", "DOES_NOT_APPLY", False) in states and sum(r.record_to_seq is None for r in _revisions(lineage)) == 1


def test_uncertain_newer_evaluation_blocks_re_adoption(client, world):
    """A flagged review cannot be decided on an UNCERTAIN evaluation (DOM-INV-03); the adoption stands until facts are
    resolved."""
    x = _applies_subject(client, "uncertain")
    assert _attest(client, x["lineage"]).status_code == 200
    from app.db import AdminSession
    from app.engines.facts import assert_fact
    from app.models import FactDefinition, Subject
    with AdminSession() as db:  # withdraw the entity type: the next evaluation is UNCERTAIN
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == "entity.financial_entity_type")).scalars().one()
        assert_fact(db, tenant_id=TA, subject=db.get(Subject, x["sid"]), fd=fd, value=None, valid_from=NOW() - timedelta(days=1),
                    valid_to=None, provenance_class="P3_CUSTOMER_ATTESTED", source_ref=None, actor_id=CCO,
                    change_kind="WITHDRAWAL", reason="Withdrawn pending confirmation")
        db.commit()
    assert _evaluate(client, x["sid"])["outcome"] == "UNCERTAIN"
    [flagged] = _current(x["lineage"])
    assert flagged.needs_review and flagged.state == "ADOPTED"
    before = db_fingerprint()
    r = _attest(client, x["lineage"])
    assert r.status_code == 409 and "UNCERTAIN" in r.text
    assert db_fingerprint() == before
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED"]


# ------------------------------------------------------------------------------------------------ authorisation, tenancy, rollback
def test_unauthorised_actors_and_other_tenants_are_refused_without_writes(client, world):
    x = _applies_subject(client, "authz")
    for actor, call, status in ((ANALYST, lambda a: _attest(client, x["lineage"], actor=a), 403),   # no attest
                                (AUDITOR, lambda a: _challenge(client, x["lineage"], actor=a), 403),  # no challenge
                                (AGENT, lambda a: _attest(client, x["lineage"], actor=a), 403),     # AI never adopts
                                (CCO_B, lambda a: _attest(client, x["lineage"], actor=a), 404),     # other tenant
                                (CCO_B, lambda a: _challenge(client, x["lineage"], actor=a), 404)):
        before = db_fingerprint()
        r = call(actor)
        assert r.status_code == status, (actor, r.status_code, r.text)
        assert db_fingerprint() == before, actor
    assert [c.state for c in _current(x["lineage"])] == ["PROPOSED"]


def test_failure_after_writes_rolls_the_whole_adoption_back(client, world, monkeypatch):
    """APP §06: attestation, determination revision, obligation and events commit together or not at all."""
    from app.engines import applicability as appl
    x = _applies_subject(client, "rollback")

    def boom(*a, **k):
        raise HTTPException(503, "simulated failure after the attestation was written")
    monkeypatch.setattr(appl, "_activate_obligation", boom)
    before = db_fingerprint()
    assert _attest(client, x["lineage"]).status_code == 503
    assert db_fingerprint() == before
    monkeypatch.undo()
    assert _attest(client, x["lineage"]).status_code == 200


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and advisory locks; exact reason required by the CI gate
    return True


def _session():
    from app.db import SessionLocal, set_principal
    db = SessionLocal()
    set_principal(db, TA, CCO)
    return db


def _ctx(db):
    from app.models import Actor
    from app.security import Ctx
    return Ctx(db=db, actor=db.get(Actor, CCO))


def _engine_attest(lineage, decision="ADOPT", reason=None):
    from app.engines import applicability as appl
    with _session() as db:
        appl.attest(db, ctx=_ctx(db), determination_lineage_id=lineage, decision=decision,
                    authority_basis="Board delegation OLI", reason=reason)
        db.commit()


def _engine_evaluate(sid):
    from app.engines import applicability as appl
    from app.models import ObligationDefinition, Subject
    with _session() as db:
        ev = appl.evaluate(db, tenant_id=TA, subject=db.get(Subject, sid), odef=db.get(ObligationDefinition, S["odef"]),
                           effective_at=NOW(), record_cutoff_seq=None, trigger="EXPLICIT_REQUEST", actor_id=CCO)
        db.commit()
        return ev.evaluation_id


def _router_challenge(lineage):
    from app.routers.applicability import ChallengeIn, challenge
    with _session() as db:
        return challenge(lineage, ChallengeIn(challenge_class="FACT_WRONG", detail="race"), _ctx(db))["challenge_id"]


def _race(monkeypatch, target: str, first, second):
    """Run `first` in thread A, held inside `target` (an engine function it reaches after taking the decision lock), then
    `second` in thread B. B must still be waiting while A holds the lock; A is then released. Returns both results."""
    from app.engines import applicability as appl
    held, release = threading.Event(), threading.Event()
    real = getattr(appl, target)

    def holding(*a, **k):
        if threading.current_thread().name == "A":
            held.set()
            assert release.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(appl, target, holding)
    out = {}

    def run(name, fn):
        try:
            out[name] = ("ok", fn())
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001 - reported by the assertions
            out[name] = ("error", repr(e))
    a = threading.Thread(target=run, args=("A", first), name="A")
    a.start()
    assert held.wait(30), "thread A never reached the critical section"
    b = threading.Thread(target=run, args=("B", second), name="B")
    b.start()
    b.join(1.5)
    waited = b.is_alive()  # B is blocked on the decision lock A holds
    release.set()
    a.join(60)
    b.join(60)
    return out, waited


def test_evaluation_waits_for_an_adoption_in_progress_and_flags_it(client, world, pg, monkeypatch):
    """Regression guard (APP §15, one current revision): the evaluation reads the current determination only after the
    decision-scope lock, so it sees the committed adoption and flags it. Before this change the order was kept only
    incidentally, by the cell record-sequence row lock it happened to take before that read; the challenge, decision and
    duplicate-evaluation races below had no such protection (verified by running them with the lock disabled)."""
    x = _applies_subject(client, "race-eval")
    from app.db import AdminSession
    from app.engines.facts import assert_fact
    from app.models import FactDefinition, Subject
    with AdminSession() as db:  # the fact changes without an evaluation yet
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == "entity.financial_entity_type")).scalars().one()
        assert_fact(db, tenant_id=TA, subject=db.get(Subject, x["sid"]), fd=fd, value="PAYMENT_INSTITUTION",
                    valid_from=NOW() - timedelta(days=1), valid_to=None, provenance_class="P3_CUSTOMER_ATTESTED",
                    source_ref=None, actor_id=CCO, change_kind="PROSPECTIVE_CHANGE", reason="race")
        db.commit()
    out, waited = _race(monkeypatch, "_activate_obligation", lambda: _engine_attest(x["lineage"]),
                        lambda: _engine_evaluate(x["sid"]))
    assert waited and out["A"][0] == "ok" and out["B"][0] == "ok", out
    [cur] = _current(x["lineage"])
    assert cur.state == "ADOPTED" and cur.needs_review and cur.outcome == "APPLIES"
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED"]


def test_challenge_waits_for_an_adoption_in_progress(client, world, pg, monkeypatch):
    x = _applies_subject(client, "race-challenge")
    out, waited = _race(monkeypatch, "_activate_obligation", lambda: _engine_attest(x["lineage"]),
                        lambda: _router_challenge(x["lineage"]))
    assert waited and out["A"][0] == "ok" and out["B"][0] == "ok", out
    assert [c.state for c in _current(x["lineage"])] == ["ADOPTED"]  # the challenge is recorded, no IN_REVIEW fork
    assert len(_events(x["lineage"], "determination.challenged")) == 1


def test_concurrent_adopt_and_reject_of_one_review_decide_once(client, world, pg, monkeypatch):
    x = _applies_subject(client, "race-decide")
    assert _challenge(client, x["lineage"]).status_code == 200
    out, waited = _race(monkeypatch, "_new_det_revision", lambda: _engine_attest(x["lineage"], "REJECT", reason="No"),
                        lambda: _engine_attest(x["lineage"], "ADOPT"))
    assert waited and out["A"][0] == "ok" and out["B"][0] == 409, out
    assert [c.state for c in _current(x["lineage"])] == ["REJECTED"]
    assert _obligations(x["sid"]) == []
    assert len(_events(x["lineage"], "determination.rejected")) == 1 and not _events(x["lineage"], "determination.adopted")


def test_concurrent_identical_evaluations_collapse_to_one(client, world, pg, monkeypatch):
    """APP-REQ-016: the second identical request returns the first's evaluation instead of failing on the unique key."""
    x = _applies_subject(client, "race-identical", evaluate=False)
    out, waited = _race(monkeypatch, "_derive", lambda: _engine_evaluate(x["sid"]), lambda: _engine_evaluate(x["sid"]))
    assert waited and out["A"][0] == "ok" and out["B"] == out["A"], out
    from app.db import admin_engine
    with admin_engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM applicability_evaluation WHERE subject_id = :s"), {"s": x["sid"]}).scalar()
        d = c.execute(text("SELECT count(*) FROM applicability_determination_revision WHERE subject_id = :s"),
                      {"s": x["sid"]}).scalar()
    assert (n, d) == (1, 1)


def test_concurrent_adoptions_record_one_decision_and_one_obligation(client, world, pg, monkeypatch):
    x = _applies_subject(client, "race-adopt")
    out, waited = _race(monkeypatch, "_activate_obligation", lambda: _engine_attest(x["lineage"]),
                        lambda: _engine_attest(x["lineage"]))
    assert waited and out["A"][0] == "ok" and out["B"][0] == 409, out
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED"]
    assert len(_events(x["lineage"], "determination.adopted")) == 1
    assert len(_audits(x["lineage"], "determination.attest")) == 1
