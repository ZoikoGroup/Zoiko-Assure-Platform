"""ObligationInstance lifecycle integrity: the transitions the code permits, refusals, terminal states, repeats,
concurrency, rollback, provenance, control mappings and history. Complements tests/test_obligation_lifecycle_integrity.py.

Established from the code (not assumed): an ObligationInstance changes only through the attestation of its determination
(POST /applicability/determinations/{lineage}/attest -> engines/applicability.attest); no obligation route writes its
lifecycle. Its stored lifecycle values are ADOPTED and CLOSED, each change a new bitemporal revision of one lineage:
  * none -> ADOPTED       ADOPT of an APPLIES determination (PROPOSED, IN_REVIEW, or a flagged ADOPTED one);
  * ADOPTED -> ADOPTED    re-ADOPT of a flagged ADOPTED determination whose latest evaluation still APPLIES (a revision on
                          the new decision, same lineage and valid start);
  * ADOPTED -> CLOSED     ADOPT of a DOES_NOT_APPLY result (prospective: valid_to = the decision's valid time);
  * CLOSED -> ADOPTED     ADOPT of a later APPLIES result (never re-claiming the closed interval, F-06).
Determination transitions follow ZA-GLS-001 §05 (enforced in ATTESTABLE). DOM §6.3 also names SUSPENDED and an
"authorized closure"; neither is implemented and neither is invented here.

Fixed here: a request to attest a determination no attestation decision leaves (ADOPTED and not flagged, REJECTED) was
refused with a bare sentence, unlike every other transition refusal of the endpoint; it now carries the same code
(DETERMINATION_TRANSITION_NOT_PERMITTED, from_state, decision) with the same status and message.
Concurrency tests run on PostgreSQL 17 only: one thread is held inside the critical section while the other is shown to
wait. Runs on SQLite and PostgreSQL 17.
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
INSTR = f"OTI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-oti-{TAG.lower()}", f"oti-cco-{TAG.lower()}"
CONTROL = f"CTRL-{TAG}-OTI"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
REFUSED = "DETERMINATION_TRANSITION_NOT_PERMITTED"


# ------------------------------------------------------------------------------------------------ world
def _ast(t: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{t}_{nid}", "operator": "EQ", "source_mappings": [f"sm_{t}_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{t}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{t}_{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        pred("eu", EU, "BOOLEAN", True), pred("kind", KIND, "ENUM", "CREDIT_INSTITUTION")]}


def _vectors() -> list[dict]:
    def vec(vid, cls, eu, kind, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {EU: eu, KIND: kind}, "expected_outcome": out}
    return [vec("P", "POSITIVE", True, "CREDIT_INSTITUTION", "APPLIES"),
            vec("N", "NEGATIVE", False, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
            vec("U", "UNKNOWN", True, {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]


def _definition(name: str) -> str:
    """A material definition over the module's one provision, mapped to the module's control by its template."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"OTI_{TAG}_{name}", title=f"Transition obligation {name}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True, satisfaction_template={"node_type": "CONTROL", "node_id": "c",
                                                                       "control_key": CONTROL})
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _publish(client, odef: str) -> None:
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": S["rv"]["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(t), "golden_vectors": _vectors(),
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"oti-{secrets.token_hex(4)}", "changelog": "oti"})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import ControlDefinition, Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Transition Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=CONTROL, title="Transition control", objective="Probe.", category="RISK_MANAGEMENT"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="OTI CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Transition Act",
        "locator": f"OJ L {TAG}/oti", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"OTI-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code == 201, rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["odef"], S["odef2"] = _definition("A"), _definition("B")  # two definitions of one requirement
    _publish(client, S["odef"])
    _publish(client, S["odef2"])
    return True


def _subject(name: str, *, eu=True) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    _fact(sid, EU, eu)
    _fact(sid, KIND, "CREDIT_INSTITUTION")
    return sid


def _fact(sid, key, value, *, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=1 if change else 40)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "Changed"}
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text


def _evaluate(sid, odef=None):
    r = S["client"].post("/api/applicability/evaluations", headers=H(CCO),
                         json={"subject_id": sid, "obligation_definition_id": odef or S["odef"]})
    assert r.status_code == 200, r.text
    return r.json()


def _proposed(name, *, eu=True, odef=None) -> dict:
    sid = _subject(name, eu=eu)
    ev = _evaluate(sid, odef)
    assert ev["current_determination"]["state"] == "PROPOSED"
    return {"sid": sid, "ev": ev, "lineage": ev["current_determination"]["lineage_id"]}


def _attest(lineage, decision="ADOPT", **extra):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO),
                            json={"decision": decision, "authority_basis": "Board delegation OTI", **extra})


def _challenge(lineage, cls="FACT_WRONG", detail="The entity type is wrong"):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/challenge", headers=H(CCO),
                            json={"challenge_class": cls, "detail": detail})


def _current(lineage):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        return db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.lineage_id == lineage,
                                                                   ApplicabilityDetermination.record_to_seq.is_(None))
                          ).scalars().all()


def _obligations(sid, odef=None):
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        q = select(ObligationInstance).where(ObligationInstance.subject_id == sid)
        if odef:
            q = q.where(ObligationInstance.obligation_definition_id == odef)
        return db.execute(q.order_by(ObligationInstance.record_from_seq)).scalars().all()


def _events(aggregate_id, event_type):
    import json

    from app.db import admin_engine
    with admin_engine.connect() as c:
        rows = c.execute(text("SELECT payload FROM platform_outbox_event WHERE aggregate_id = :a AND event_type = :t "
                              "ORDER BY record_seq, event_id"), {"a": aggregate_id, "t": event_type}).all()
    return [p if isinstance(p, dict) else json.loads(p) for (p,) in rows]


def _audits(object_id, action):
    from app.db import admin_engine
    with admin_engine.connect() as c:
        return c.execute(text("SELECT prior_state, new_state FROM platform_audit_event WHERE object_id = :o AND action = :a "
                              "ORDER BY record_seq"), {"o": object_id, "a": action}).all()


def _refused(r, from_state, decision="ADOPT") -> bool:
    d = r.json().get("detail")
    return r.status_code == 409 and isinstance(d, dict) and (d.get("error"), d.get("from_state"), d.get("decision")) == \
        (REFUSED, from_state, decision)


def _flag(x, kind):
    """A newer evaluation with another result flags the adopted determination (APP §11 C)."""
    _fact(x["sid"], KIND, kind, change=True)
    [cur] = _current(x["lineage"])
    assert cur.state == "ADOPTED" and cur.needs_review, (cur.state, cur.needs_review)
    return cur


def _dossier(lineage, as_of=None):
    r = S["client"].get(f"/api/obligations/{lineage}", headers=H(CCO),
                        params={"as_of_record_seq": as_of} if as_of is not None else None)
    assert r.status_code == 200, r.text
    return r.json()


def _chain(d) -> tuple:
    """The regulatory chain a dossier resolves: rule version, release, requirement versions and their bound sources."""
    p = d["regulatory_provenance"]
    return (p["rule_version_id"], p["content_release_id"],
            sorted((q["requirement"]["requirement_version_id"], q["bound_source_fingerprint"]) for q in p["requirements"]))


def _mappings(d) -> list:
    return [(e["expression_version_id"], e["version"], e["ast_hash"]) for e in d["expression_versions"]]


# ------------------------------------------------------------------------------------------------ permitted transitions
def test_adopting_a_does_not_apply_determination_records_the_decision_without_an_obligation(client, world):
    x = _proposed("dna", eu=False)
    assert x["ev"]["outcome"] == "DOES_NOT_APPLY"
    r = _attest(x["lineage"])
    assert r.status_code == 200, r.text
    assert (r.json()["determination"]["state"], r.json()["determination"]["outcome"]) == ("ADOPTED", "DOES_NOT_APPLY")
    assert r.json()["obligation"] is None and _obligations(x["sid"]) == []
    [adopted] = _events(x["lineage"], "determination.adopted")
    assert adopted["obligation_lineage_id"] is None and adopted["obligation_revision_id"] is None
    # Decided: a repeat is refused with the transition code (it was a bare sentence) and writes nothing.
    before = db_fingerprint()
    again = _attest(x["lineage"])
    assert _refused(again, "ADOPTED"), again.text
    assert again.json()["detail"]["message"] == "Determination in state ADOPTED cannot be attested"
    assert db_fingerprint() == before


def test_an_in_review_determination_adopts_into_one_obligation_with_its_mapping(client, world):
    x = _proposed("in-review")
    assert _challenge(x["lineage"]).status_code == 200
    assert [c.state for c in _current(x["lineage"])] == ["IN_REVIEW"]
    r = _attest(x["lineage"])
    assert r.status_code == 200, r.text
    assert _audits(x["lineage"], "determination.attest")[-1] == ("IN_REVIEW", "ADOPTED")
    [o] = _obligations(x["sid"])
    assert (o.lifecycle, o.record_to_seq, o.determination_revision_id) == ("ADOPTED", None, r.json()["determination"]["revision_id"])
    d = _dossier(o.lineage_id)
    assert [(v, h) for _, v, h in _mappings(d)] and [m[1] for m in _mappings(d)] == [1]
    assert [c["control_key"] for c in d["controls"]] == [CONTROL]


def test_a_flagged_adoption_re_adopted_on_the_same_outcome_revises_the_obligation_in_place(client, world):
    """ADOPTED -> ADOPTED: an UNCERTAIN evaluation flags the adoption; once the facts are back the result APPLIES again
    and the re-adoption is a new revision of the same obligation: same lineage and valid start, the new decision, the
    control mapping untouched (no second expression version)."""
    x = _proposed("same-outcome")
    first = _attest(x["lineage"]).json()["obligation"]
    from app.db import AdminSession
    from app.engines.facts import assert_fact
    from app.models import FactDefinition, Subject
    with AdminSession() as db:  # withdraw the entity type: the next evaluation is UNCERTAIN and flags the adoption
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == KIND)).scalars().one()
        assert_fact(db, tenant_id=TA, subject=db.get(Subject, x["sid"]), fd=fd, value=None,
                    valid_from=NOW() - timedelta(days=2), valid_to=None, provenance_class="P3_CUSTOMER_ATTESTED",
                    source_ref=None, actor_id=CCO, change_kind="WITHDRAWAL", reason="Pending confirmation")
        db.commit()
    assert _evaluate(x["sid"])["outcome"] == "UNCERTAIN"
    _fact(x["sid"], KIND, "CREDIT_INSTITUTION", change=True)  # restored: re-evaluated, APPLIES again
    [flagged] = _current(x["lineage"])
    assert flagged.needs_review and flagged.outcome == "APPLIES"
    mappings = _mappings(_dossier(first["lineage_id"]))
    r = _attest(x["lineage"], revision_id=flagged.revision_id)
    assert r.status_code == 200, r.text
    revs = _obligations(x["sid"])
    assert [o.lifecycle for o in revs] == ["ADOPTED", "ADOPTED"] and len({o.lineage_id for o in revs}) == 1
    assert [o.record_to_seq is None for o in revs] == [False, True]
    assert revs[1].valid_from == revs[0].valid_from and revs[1].determination_revision_id == r.json()["determination"]["revision_id"]
    assert _mappings(_dossier(first["lineage_id"])) == mappings
    assert len(_events(first["lineage_id"], "obligation.adopted")) == 2


def test_closure_and_reopening_keep_one_lineage_mappings_provenance_and_history(client, world):
    """ADOPTED -> CLOSED -> ADOPTED through the governed adoption workflow only. A CLOSED obligation is not reopened by a
    repeated attestation, a challenge or an evaluation with the same result; the regulatory chain, the control mapping,
    the decision history and every evaluation's replay are the same before and after each transition."""
    x = _proposed("close-reopen")
    adopted = _attest(x["lineage"]).json()["obligation"]
    lineage = adopted["lineage_id"]
    d0 = _dossier(lineage)
    chain, mappings, controls = _chain(d0), _mappings(d0), d0["controls"]
    assert d0["regulatory_provenance"]["integrity"]["status"] == "INTACT", d0["regulatory_provenance"]["integrity"]
    assert S["rv"]["requirement_version_id"] in [r for r, _ in chain[2]]

    flagged = _flag(x, "PAYMENT_INSTITUTION")
    r = _attest(x["lineage"], revision_id=flagged.revision_id)
    assert r.status_code == 200, r.text
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED", "CLOSED"]
    closed_seq = _obligations(x["sid"])[-1].record_from_seq
    # Terminal for ordinary requests: none of these writes anything or reopens it.
    before = db_fingerprint()
    assert _refused(_attest(x["lineage"]), "ADOPTED")
    assert _refused(_attest(x["lineage"], "REJECT", reason="No"), "ADOPTED", "REJECT")
    assert db_fingerprint() == before
    assert _challenge(x["lineage"], "INTERPRETATION").status_code == 200  # recorded, no state change (APP-REQ-011)
    assert _evaluate(x["sid"])["outcome"] == "DOES_NOT_APPLY"             # same result: nothing to review
    [cur] = _current(x["lineage"])
    assert (cur.state, cur.needs_review) == ("ADOPTED", False)
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED", "CLOSED"]
    assert len(_events(lineage, "obligation.revised")) == 1
    d1 = _dossier(lineage)
    assert (_chain(d1), _mappings(d1), d1["controls"], d1["obligation"]["lifecycle"]) == (chain, mappings, controls, "CLOSED")

    flagged = _flag(x, "CREDIT_INSTITUTION")  # the governed way back: a newer APPLIES result, adopted
    assert _attest(x["lineage"], revision_id=flagged.revision_id).status_code == 200
    revs = _obligations(x["sid"])
    assert [o.lifecycle for o in revs] == ["ADOPTED", "CLOSED", "ADOPTED"] and len({o.lineage_id for o in revs}) == 1
    assert sum(o.record_to_seq is None for o in revs) == 1
    d2 = _dossier(lineage)
    assert (_chain(d2), _mappings(d2), d2["controls"], d2["obligation"]["lifecycle"]) == (chain, mappings, controls, "ADOPTED")

    # History: each revision is the current one as known at its own record sequence, and reads are stable.
    for rev in revs:
        h1, h2 = _dossier(lineage, rev.record_from_seq), _dossier(lineage, rev.record_from_seq)
        assert h1["obligation"]["revision_id"] == rev.revision_id and h1["obligation"]["lifecycle"] == rev.lifecycle
        assert h1["decision_history"] == h2["decision_history"] and _chain(h1) == chain
    hist = d2["decision_history"]
    assert hist["integrity"]["status"] == "INTACT", hist["integrity"]
    assert [h["lifecycle"] for h in hist["revisions"]] == ["ADOPTED", "CLOSED", "ADOPTED"]
    assert _dossier(lineage, closed_seq)["decision_history"]["revisions"][1]["closure"] is not None
    # Every evaluation behind these decisions still replays to its recorded result.
    evs = client.get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": x["sid"]}).json()
    assert len(evs) >= 3
    for ev in evs:
        v = client.get(f"/api/applicability/evaluations/{ev['evaluation_id']}", headers=H(CCO)).json()
        assert v["replay"]["status"] == "REPRODUCED", (ev["evaluation_id"], v["replay"])


# ------------------------------------------------------------------------------------------------ refusals and rollback
def test_invalid_attestations_and_challenges_are_refused_with_structured_errors_and_write_nothing(client, world):
    p = _proposed("refuse-p")["lineage"]
    r = _proposed("refuse-r")["lineage"]
    assert _challenge(r).status_code == 200                        # IN_REVIEW
    j = _proposed("refuse-j")["lineage"]
    assert _challenge(j).status_code == 200
    assert _attest(j, "REJECT", reason="Declined").status_code == 200  # REJECTED
    a = _proposed("refuse-a")["lineage"]
    assert _attest(a).status_code == 200                           # ADOPTED

    def post(lineage, body, path="attest"):
        return client.post(f"/api/applicability/determinations/{lineage}/{path}", headers=H(CCO), json=body)

    def validation(field):
        return lambda r: r.status_code == 422 and isinstance(r.json()["detail"], list) and \
            any(field in e["loc"] for e in r.json()["detail"])

    def sentence(status, words):
        return lambda r: r.status_code == status and words in str(r.json()["detail"])

    def coded(status, code):
        return lambda r: r.status_code == status and isinstance(r.json()["detail"], dict) and r.json()["detail"].get("error") == code
    ok = {"decision": "ADOPT", "authority_basis": "Board"}
    cases = [
        (p, {**ok, "decision": "REVOKE"}, "attest", sentence(422, "ADOPT or REJECT")),
        (p, {**ok, "decision": "adopt"}, "attest", sentence(422, "ADOPT or REJECT")),
        (p, {"authority_basis": "Board"}, "attest", validation("decision")),
        (p, {**ok, "authority_basis": "   "}, "attest", sentence(422, "authority_basis is required")),
        (p, {**ok, "authority_basis": "x" * 201}, "attest", validation("authority_basis")),
        (p, {**ok, "revision_id": "x" * 37}, "attest", validation("revision_id")),
        (p, {**ok, "valid_from": "2027-01-01"}, "attest", validation("valid_from")),
        (p, {**ok, "decision": "REJECT", "reason": "No"}, "attest", lambda r: _refused(r, "PROPOSED", "REJECT")),
        (r, {**ok, "decision": "REJECT"}, "attest", sentence(422, "Rejection reason is mandatory")),
        (r, {**ok, "decision": "REJECT", "reason": "   "}, "attest", sentence(422, "Rejection reason is mandatory")),
        (j, ok, "attest", lambda r: _refused(r, "REJECTED")),
        (j, {**ok, "decision": "REJECT", "reason": "Again"}, "attest", lambda r: _refused(r, "REJECTED", "REJECT")),
        (a, ok, "attest", lambda r: _refused(r, "ADOPTED")),
        (a, {**ok, "decision": "REJECT", "reason": "Late"}, "attest", lambda r: _refused(r, "ADOPTED", "REJECT")),
        ("no-such-lineage", ok, "attest", lambda r: r.status_code == 404),
        (p, {"challenge_class": "NOPE", "detail": "x"}, "challenge", coded(422, "CHALLENGE_CLASS_INVALID")),
        (p, {"challenge_class": "FACT_WRONG", "detail": "   "}, "challenge", coded(422, "CHALLENGE_DETAIL_REQUIRED")),
    ]
    before = db_fingerprint()
    failures = [(lin, body, path, x.status_code, x.text[:200]) for lin, body, path, check in cases
                if not check(x := post(lin, body, path))]
    assert not failures, failures
    assert db_fingerprint() == before


def test_a_failed_closure_rolls_back_the_whole_decision(client, world, monkeypatch):
    """APP §06: the attestation, the determination revision, the CLOSED obligation revision and the events commit
    together or not at all; the obligation stays ADOPTED and the decision can be made again."""
    from app.engines import applicability as appl
    x = _proposed("close-rollback")
    assert _attest(x["lineage"]).status_code == 200
    flagged = _flag(x, "PAYMENT_INSTITUTION")
    real = appl._close_obligation

    def close_then_fail(*a, **k):
        real(*a, **k)  # the closure is written in the transaction, then the request fails
        raise HTTPException(503, "simulated failure after the closure was written")
    monkeypatch.setattr(appl, "_close_obligation", close_then_fail)
    before = db_fingerprint()
    assert _attest(x["lineage"], revision_id=flagged.revision_id).status_code == 503
    assert db_fingerprint() == before
    monkeypatch.undo()
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED"]
    assert _attest(x["lineage"], revision_id=flagged.revision_id).status_code == 200
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED", "CLOSED"]


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and row/advisory locks; reason required by the CI gate
    return True


def _session():
    from app.db import SessionLocal, set_principal
    db = SessionLocal()
    set_principal(db, TA, CCO)
    return db


def _engine_attest(lineage):
    from app.engines import applicability as appl
    from app.models import Actor
    from app.security import Ctx
    with _session() as db:
        r = appl.attest(db, ctx=Ctx(db=db, actor=db.get(Actor, CCO)), determination_lineage_id=lineage,
                        decision="ADOPT", authority_basis="Board delegation OTI")
        db.commit()
        return r["determination"].revision_id


def _race(monkeypatch, target: str, first, second):
    """`first` runs in thread A and is held inside applicability.`target` (reached after the decision lock and the record
    sequence); `second` runs in thread B, which must still be waiting while A is held. Returns both results."""
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
    waited = b.is_alive()
    release.set()
    a.join(60)
    b.join(60)
    return out, waited


def test_concurrent_closures_of_one_review_close_once(client, world, pg, monkeypatch):
    x = _proposed("race-close")
    lineage = _attest(x["lineage"]).json()["obligation"]["lineage_id"]
    _flag(x, "PAYMENT_INSTITUTION")
    out, waited = _race(monkeypatch, "_close_obligation", lambda: _engine_attest(x["lineage"]),
                        lambda: _engine_attest(x["lineage"]))
    assert waited and out["A"][0] == "ok" and out["B"][0] == 409, out
    assert (out["B"][1]["error"], out["B"][1]["from_state"]) == (REFUSED, "ADOPTED"), out
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED", "CLOSED"]
    assert len(_events(lineage, "obligation.revised")) == 1


def test_concurrent_reopenings_reopen_once(client, world, pg, monkeypatch):
    x = _proposed("race-reopen")
    assert _attest(x["lineage"]).status_code == 200
    flagged = _flag(x, "PAYMENT_INSTITUTION")
    assert _attest(x["lineage"], revision_id=flagged.revision_id).status_code == 200  # CLOSED
    _flag(x, "CREDIT_INSTITUTION")
    out, waited = _race(monkeypatch, "_activate_obligation", lambda: _engine_attest(x["lineage"]),
                        lambda: _engine_attest(x["lineage"]))
    assert waited and out["A"][0] == "ok" and out["B"][0] == 409 and out["B"][1]["error"] == REFUSED, out
    revs = _obligations(x["sid"])
    assert [o.lifecycle for o in revs] == ["ADOPTED", "CLOSED", "ADOPTED"] and sum(o.record_to_seq is None for o in revs) == 1


def test_a_snapshot_racing_a_closure_reads_one_consistent_state(client, world, pg, monkeypatch):
    """A satisfaction evaluation (persisted snapshot) running while a closure is being committed answers wholly from the
    knowledge before it: its record cutoff precedes the closure, it names the ADOPTED revision, and it reproduces; the
    closure is what later reads see. Nothing reads the closure half-way. (How a snapshot computed after a prospective
    closure treats the period before it is an open engine question, deliberately not asserted here.)"""
    from app.engines import assurance as asr
    x = _proposed("race-snapshot")
    adopted = _attest(x["lineage"]).json()["obligation"]
    flagged = _flag(x, "PAYMENT_INSTITUTION")
    start = datetime.fromisoformat(adopted["valid_from"].replace("Z", "+00:00"))
    period = (start, start + timedelta(days=1))

    def snapshot():
        with _session() as db:
            r = asr.compute_snapshot(db, tenant_id=TA, obligation_lineage_id=adopted["lineage_id"], period_from=period[0],
                                     period_to=period[1], record_cutoff_seq=None, perspective="RECONSTRUCTED_NOW",
                                     actor_id=CCO)
            db.commit()
            return r["snapshot_id"], r["obligation_revision_id"]
    out, waited = _race(monkeypatch, "_close_obligation", lambda: _engine_attest(x["lineage"]), snapshot)
    assert waited and out["A"][0] == "ok" and out["B"][0] == "ok", out
    snap_id, revision = out["B"][1]
    closed = _obligations(x["sid"])[-1]
    assert closed.lifecycle == "CLOSED" and revision == adopted["revision_id"] != closed.revision_id
    snap = client.get(f"/api/snapshots/{snap_id}", headers=H(CCO)).json()
    assert snap["record_cutoff_seq"] < closed.record_from_seq
    assert client.get(f"/api/snapshots/{snap_id}/reproduce", headers=H(CCO)).json()["status"] == "REPRODUCED"
    now = _dossier(adopted["lineage_id"])["obligation"]
    assert (now["revision_id"], now["lifecycle"]) == (closed.revision_id, "CLOSED")
    assert flagged.revision_id != _current(x["lineage"])[0].revision_id


def test_obligations_of_two_definitions_for_one_requirement_are_generated_independently(client, world, pg, monkeypatch):
    """One requirement, two obligation definitions: concurrent adoptions for one subject each create their own
    obligation (one per subject and definition, DOM §13), neither refused nor merged, each traced to the same requirement."""
    sid = _subject("race-two-defs")
    lineages = [_evaluate(sid, odef)["current_determination"]["lineage_id"] for odef in (S["odef"], S["odef2"])]
    out, _ = _race(monkeypatch, "_activate_obligation", lambda: _engine_attest(lineages[0]),
                   lambda: _engine_attest(lineages[1]))
    assert out["A"][0] == "ok" and out["B"][0] == "ok", out
    for odef in (S["odef"], S["odef2"]):
        [o] = _obligations(sid, odef)
        assert o.lifecycle == "ADOPTED"
        d = _dossier(o.lineage_id)
        assert S["rv"]["requirement_version_id"] in [r for r, _ in _chain(d)[2]]
        assert d["regulatory_provenance"]["integrity"]["status"] == "INTACT"
    assert len({o.lineage_id for o in _obligations(sid)}) == 2
