"""Obligation closure valid-time integrity (TIM-REQ-002): adopting a non-APPLIES determination never stores a CLOSED
obligation revision whose end is at or before its start.

Controlling text:
  * ZA-TIM-001 v1.0 §24, TIM-REQ-002: "All valid ranges MUST use canonical half-open [start, end) semantics".
  * ZA-APP-001 v1.0 §11 C step 6: "Prospectively/retroactively revise obligation according to valid-time policy;
    never delete history." The retroactive valid-time policy is not yet decided.
  * ZA-DAT-001 DAT-REQ-009: corrections preserve closed record slices and create new revisions.

Policy implemented: refuse the closure until a valid-time policy for it is specified. When the closing determination's
valid time starts at or before the obligation's start, the attestation is refused with 409
CLOSURE_PRECEDES_OBLIGATION_START and nothing is written: no attestation, determination revision, obligation revision,
audit row or event. The determination stays flagged for review. Closures dated after the start keep today's behaviour:
a CLOSED revision [start, closing determination's valid-from). No migration, data repair or database constraint.

Before the fix the CLOSED revision was written as [start, earlier date): an inverted range.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"OCV-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-ocv-{TAG.lower()}", f"ocv-cco-{TAG.lower()}"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
REFUSED = "CLOSURE_PRECEDES_OBLIGATION_START"
S: dict = {}


def NOW() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


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


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Closure Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="OCV CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Closure Act",
        "locator": f"OJ L {TAG}/ocv", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"OCV-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code == 201, rv.text
    req = rv.json()
    assert client.post(f"/api/catalog/requirements/{req['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"OCV_{TAG}_A", title="Closure obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in req["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    t, p = secrets.token_hex(3), req["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": req["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": src.json()["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(t), "golden_vectors": _vectors(),
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"ocv-{secrets.token_hex(4)}", "changelog": "ocv"})
    assert r.status_code == 200, r.text
    return True


# ------------------------------------------------------------------------------------------------ helpers
def _subject() -> str:
    """A fresh legal entity of the module's organisation, established in the EU and a credit institution from 40 days
    ago (so the rule APPLIES today)."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    n = secrets.token_hex(4)
    sid, eid = f"s-ocv-{n}", f"e-ocv-{n}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"OCV {n} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY",
                       display_name=f"OCV {n} Ltd"))
        db.commit()
    for key, value in ((EU, True), (KIND, "CREDIT_INSTITUTION")):
        _fact(sid, key, value, NOW() - timedelta(days=40))
    return sid


def _fact(sid, key, value, valid_from, valid_to=None, change_kind="CREATE"):
    body = {"fact_key": key, "value": value, "valid_from": valid_from.isoformat(), "change_kind": change_kind}
    if valid_to is not None:
        body["valid_to"] = valid_to.isoformat()
    if change_kind != "CREATE":
        body["reason"] = f"{change_kind.lower()} for the closure test"
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text


def _evaluate(sid, at=None) -> dict:
    body = {"subject_id": sid, "obligation_definition_id": S["odef"]}
    if at is not None:
        body["effective_at"] = at if isinstance(at, str) else at.isoformat()
    r = S["client"].post("/api/applicability/evaluations", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _attest(lineage, revision_id=None, key=None):
    body = {"decision": "ADOPT", "authority_basis": "Board delegation OCV"}
    if revision_id:
        body["revision_id"] = revision_id
    headers = H(CCO) | ({"Idempotency-Key": key} if key else {})
    return S["client"].post(f"/api/applicability/determinations/{lineage}/attest", headers=headers, json=body)


def _adopted() -> dict:
    """A subject whose APPLIES determination is adopted today: one ADOPTED obligation, open-ended."""
    sid = _subject()
    ev = _evaluate(sid)
    assert ev["outcome"] == "APPLIES", ev
    lineage = ev["current_determination"]["lineage_id"]
    r = _attest(lineage)
    assert r.status_code == 200, r.text
    ob = r.json()["obligation"]
    return {"sid": sid, "lineage": lineage, "start": _ts(ob["valid_from"]), "start_text": ob["valid_from"]}


def _current_determination(lineage):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination as D
    with AdminSession() as db:
        [cur] = db.execute(select(D).where(D.lineage_id == lineage, D.record_to_seq.is_(None))).scalars().all()
        return cur


def _obligations(sid):
    from app.db import AdminSession
    from app.models import ObligationInstance as O
    with AdminSession() as db:
        return db.execute(select(O).where(O.subject_id == sid).order_by(O.record_from_seq)).scalars().all()


def _flagged_in_the_past(x, days_before_start: int) -> object:
    """A late-arriving fact says the entity was not established in the EU over an earlier period; an explicit evaluation
    dated in that period is DOES_NOT_APPLY and flags the adopted determination for review with an earlier valid time."""
    at = x["start"] - timedelta(days=days_before_start)
    _fact(x["sid"], EU, False, at - timedelta(days=40), at + timedelta(days=20), change_kind="LATE_ARRIVAL")
    assert _evaluate(x["sid"], at)["outcome"] == "DOES_NOT_APPLY"
    cur = _current_determination(x["lineage"])
    assert (cur.state, cur.needs_review) == ("ADOPTED", True), (cur.state, cur.needs_review)
    return cur


def _flagged_now(x) -> datetime:
    """The entity stops being a credit institution from yesterday; the newest evaluation, dated now, is DOES_NOT_APPLY
    and flags the adopted determination. Returns that evaluation's effective date, where a closure would end."""
    _fact(x["sid"], KIND, "PAYMENT_INSTITUTION", NOW() - timedelta(days=1), change_kind="PROSPECTIVE_CHANGE")
    ev = _evaluate(x["sid"])
    assert ev["outcome"] == "DOES_NOT_APPLY", ev
    assert _current_determination(x["lineage"]).needs_review
    closing_at = _ts(ev["effective_at"])
    assert closing_at > x["start"]
    return closing_at


def _refused(r, x) -> bool:
    d = r.json().get("detail")
    return (r.status_code == 409 and isinstance(d, dict) and d.get("error") == REFUSED
            and _ts(d.get("obligation_valid_from")) == x["start"])


# ------------------------------------------------------------------------------------------------ refused closures
def test_a_closure_dated_before_the_obligation_starts_is_refused_and_writes_nothing(world):
    x = _adopted()
    flagged = _flagged_in_the_past(x, 60)
    before = db_fingerprint()
    r = _attest(x["lineage"], flagged.revision_id)
    assert _refused(r, x), r.text
    assert _ts(r.json()["detail"]["closing_valid_from"]) < x["start"]
    assert db_fingerprint() == before
    assert [(o.lifecycle, o.record_to_seq) for o in _obligations(x["sid"])] == [("ADOPTED", None)]
    cur = _current_determination(x["lineage"])
    assert (cur.revision_id, cur.state, cur.needs_review) == (flagged.revision_id, "ADOPTED", True)


def test_a_closure_dated_exactly_at_the_obligation_start_is_refused(world):
    """[start, start) is empty, not a half-open range."""
    x = _adopted()
    _fact(x["sid"], EU, False, x["start"], change_kind="RETROACTIVE_CORRECTION")
    assert _evaluate(x["sid"], x["start_text"])["outcome"] == "DOES_NOT_APPLY"
    flagged = _current_determination(x["lineage"])
    assert _ts(flagged.valid_from) == x["start"] and flagged.needs_review
    before = db_fingerprint()
    assert _refused(_attest(x["lineage"], flagged.revision_id), x)
    assert db_fingerprint() == before
    assert [o.lifecycle for o in _obligations(x["sid"])] == ["ADOPTED"]


def test_a_refused_closure_is_refused_again_under_the_same_idempotency_key(world):
    """A refusal is not remembered as a result: a retry with the same key is refused again and writes nothing."""
    x = _adopted()
    flagged = _flagged_in_the_past(x, 30)
    key = f"ocv-{secrets.token_hex(6)}"
    before = db_fingerprint()
    for _ in range(2):
        assert _refused(_attest(x["lineage"], flagged.revision_id, key=key), x)
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ permitted closures
def test_a_closure_dated_after_the_start_keeps_todays_behaviour(world):
    """The CLOSED revision runs from the obligation's start to the closing evaluation's effective date (the adopted
    revision's valid-from); the ADOPTED revision is record-closed, never edited or deleted."""
    x = _adopted()
    closing_at = _flagged_now(x)
    flagged = _current_determination(x["lineage"])
    r = _attest(x["lineage"], flagged.revision_id)
    assert r.status_code == 200, r.text
    adopted, closed = _obligations(x["sid"])
    assert (adopted.lifecycle, closed.lifecycle) == ("ADOPTED", "CLOSED")
    assert adopted.record_to_seq == closed.record_from_seq and closed.record_to_seq is None
    assert _ts(closed.valid_from) == x["start"] and _ts(closed.valid_to) == closing_at
    assert _ts(closed.valid_to) > _ts(closed.valid_from)


def test_after_a_refusal_a_closure_dated_after_the_start_is_accepted(world):
    x = _adopted()
    flagged = _flagged_in_the_past(x, 45)
    assert _refused(_attest(x["lineage"], flagged.revision_id), x)
    closing_at = _flagged_now(x)
    cur = _current_determination(x["lineage"])
    r = _attest(x["lineage"], cur.revision_id)
    assert r.status_code == 200, r.text
    [*_, closed] = _obligations(x["sid"])
    assert closed.lifecycle == "CLOSED" and _ts(closed.valid_to) == closing_at > _ts(closed.valid_from) == x["start"]


# ------------------------------------------------------------------------------------------------ invariant
def test_no_obligation_revision_of_this_organisation_has_an_inverted_range(world):
    """Runs last in the module: across every closure attempted above, no stored obligation revision ends at or before
    its start."""
    from app.db import AdminSession
    from app.models import ObligationInstance as O
    with AdminSession() as db:
        revs = db.execute(select(O).where(O.tenant_id == TA)).scalars().all()
    assert revs, "the module wrote obligation revisions"
    inverted = [(o.revision_id, o.lifecycle, o.valid_from, o.valid_to) for o in revs
                if o.valid_to is not None and _ts(o.valid_to) <= _ts(o.valid_from)]
    assert not inverted, inverted
