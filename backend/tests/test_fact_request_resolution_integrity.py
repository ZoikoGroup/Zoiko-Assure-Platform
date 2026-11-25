"""Fact request resolution integrity (APP-REQ-010): a fact write resolves a FactRequest only when the fact is known
where the request's evaluations need it.

Controlling text:
  * ZA-APP-001 §18 APP-REQ-010: UNCERTAIN "MUST create or link to actionable FactRequests for material
    missing/conflicting facts according to workflow policy".
  * ZA-APP-001 §11 B step 6: "Resolve FactRequest if requirement satisfied; preserve AE1 history."
  * ZA-APP-001 §08: one open request serves the evaluations with a "compatible valid-time need".
  * ZA-APP-001 §04 fact resolution (facts.resolve_fact): policy filter, expiry, conflict -> unknown, no "latest record
    wins".

Implemented interpretation, "known where needed": an OPEN or IN_PROGRESS request is RESOLVED by a fact write only when
facts.resolve_fact, at the current record watermark and without creating conflict records, finds the fact known for
the subject and effective date of every evaluation the request links. Otherwise it stays as it is: an unknown, partly
covered, withdrawn, conflicting or expired fact, or a link that cannot be found in the organisation, leaves it open.
Decided from current knowledge; the request row keeps no history and no column is added.

Before the fix, every open request for the fact was resolved on any write (applicability.reevaluate_for_fact).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from conftest import H, add_principal

TAG = secrets.token_hex(3)
TA, TB = f"tenant-fri-a-{TAG}", f"tenant-fri-b-{TAG}"
CCO_A, CCO_B = f"fri-cco-a-{TAG}", f"fri-cco-b-{TAG}"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"FRI-{TAG.upper()}-REG"
F, FX = f"fri{TAG}.flag", f"fri{TAG}.expiring"   # FX expires one day after it is recorded
NOW = datetime.now(timezone.utc).replace(microsecond=0)
RESOLVED_TEXT = "Resolved by recorded FactAssertion; linked evaluations re-run."
W: dict = {}


def ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def ahead(days: int) -> datetime:
    return NOW + timedelta(days=days)


def _iso(d: datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------------------------------------------------ fixture
def _publish(client, key: str, fact: str, requirement: dict, src: dict) -> str:
    """ObligationDefinition `key` whose rule is the single predicate EQ(fact, True); returns its ID."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=key, title=f"Resolution {key}", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in requirement["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = f"fri{secrets.token_hex(3)}", requirement["provisions"][0]
    ast = {"node_type": "PREDICATE", "node_id": f"{t}_p", "operator": "EQ", "source_mappings": [f"sm_{t}_p"],
           "args": [{"node_type": "FACT_REF", "node_id": f"{t}_pf", "fact": fact},
                    {"node_type": "CONST", "node_id": f"{t}_pc", "type": "BOOLEAN", "value": True}]}
    maps = [{"source_mapping_id": f"sm_{t}_p", "requirement_version_id": requirement["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": src["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}]

    def vec(vid, cls, value, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {fact: value}, "expected_outcome": expected}
    body = {"obligation_definition_id": odef, "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps, "ast": ast,
            "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"fri-{secrets.token_hex(4)}", "changelog": "fact request resolution"})
    assert r.status_code == 200, r.text
    return odef


@pytest.fixture(scope="module")
def world(client):
    """Organisations A and B, the boolean facts F (no expiry) and FX (expiry 1 day), and one published rule per fact
    (EQ(fact, True)). Each test registers its own subjects."""
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, Tenant
    with AdminSession() as db:
        for t in (TA, TB):
            db.add(Tenant(tenant_id=t, legal_name=f"{t} Group", contracting_region="EU"))
        db.add(FactDefinition(key=F, label=f"label {F}", data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(FactDefinition(key=FX, label=f"label {FX}", data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED", expiry_days=1))
        for aid, tenant in ((CCO_A, TA), (CCO_B, TB)):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=["CCO"])
        db.add(Instrument(identifier=INSTR, title="Resolution Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Resolution Act",
        "locator": f"OJ L {TAG}/fri", "instrument_identifier": INSTR, "raw_text": "Entities shall keep a register."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"FRI-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    W["odef"] = {F: _publish(client, f"FRI_{TAG.upper()}_F", F, rv.json(), src.json()),
                 FX: _publish(client, f"FRI_{TAG.upper()}_X", FX, rv.json(), src.json())}
    W["client"] = client
    return True


# ------------------------------------------------------------------------------------------------------------ helpers
def _subject(tenant: str = TA) -> str:
    """A fresh legal entity of `tenant` (its own subject, so tests never share a request)."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    n = secrets.token_hex(4)
    sid, eid = f"s-fri-{n}", f"e-fri-{n}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"FRI {n} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY",
                       display_name=f"FRI {n} Ltd"))
        db.commit()
    return sid


def _actor(tenant: str) -> str:
    return CCO_A if tenant == TA else CCO_B


def _evaluate(sid, at: datetime, key: str = F, tenant: str = TA) -> dict:
    r = W["client"].post("/api/applicability/evaluations", headers=H(_actor(tenant)),
                         json={"subject_id": sid, "obligation_definition_id": W["odef"][key], "effective_at": _iso(at)})
    assert r.status_code == 200, r.text
    return r.json()


def _write(sid, valid_from: datetime, valid_to: datetime | None = None, *, key: str = F, value=True,
           change_kind: str = "CREATE", tenant: str = TA) -> dict:
    body = {"fact_key": key, "value": value, "valid_from": _iso(valid_from), "change_kind": change_kind}
    if valid_to is not None:
        body["valid_to"] = _iso(valid_to)
    if change_kind != "CREATE":
        body["reason"] = f"{change_kind.lower()} for the resolution test"
    r = W["client"].post(f"/api/subjects/{sid}/facts", headers=H(_actor(tenant)), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _requests(sid, key: str = F) -> list[dict]:
    """Every FactRequest of `sid` for `key`, oldest first, as stored."""
    from app.db import AdminSession
    from app.models import FactDefinition, FactRequest
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == key)).scalars().one()
        rows = db.execute(select(FactRequest).where(FactRequest.subject_id == sid,
                                                    FactRequest.fact_definition_id == fd.fact_definition_id)
                          .order_by(FactRequest.created_at, FactRequest.request_id)).scalars().all()
        return [{"request_id": r.request_id, "status": r.status, "resolution": r.resolution,
                 "reason_code": r.reason_code, "linked": list(r.linked_evaluation_ids or [])} for r in rows]


def _only_request(sid, key: str = F) -> dict:
    rows = _requests(sid, key)
    assert len(rows) == 1, rows
    return rows[0]


def _set_request(rid: str, **values) -> None:
    from app.db import AdminSession
    from app.models import FactRequest
    with AdminSession() as db:
        db.execute(update(FactRequest).where(FactRequest.request_id == rid).values(**values))
        db.commit()


def _second_source(sid: str, key: str, value) -> str:
    """A second current assertion of `key` for `sid` with another value over the same valid time (as a concurrent
    import would record it): two admissible candidates that disagree."""
    from app.db import AdminSession
    from app.engines.facts import typed_columns
    from app.models import FactAssertion, FactDefinition, new_id
    from app.platform import next_record_seq
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == key)).scalars().one()
        cur = db.execute(select(FactAssertion).where(FactAssertion.subject_id == sid,
                                                     FactAssertion.fact_definition_id == fd.fact_definition_id,
                                                     FactAssertion.record_to_seq.is_(None))).scalars().one()
        row = {c.name: getattr(cur, c.name) for c in FactAssertion.__table__.columns}
        rid = new_id()
        row.update(typed_columns(fd, value), revision_id=rid, lineage_id=new_id(), record_batch_id=new_id(),
                   record_from_seq=next_record_seq(db), source_ref="second-source-import")
        db.add(FactAssertion(**row))
        db.commit()
        return rid


def _view(rid: str, tenant: str = TA) -> dict:
    r = W["client"].get(f"/api/fact-requests/{rid}", headers=H(_actor(tenant)))
    assert r.status_code == 200, r.text
    return r.json()


def _needed_known(rid: str, tenant: str = TA) -> list[bool]:
    """How the read view resolves the fact today for each linked evaluation (its own valid instant)."""
    return [n["current_resolution"]["known"] for n in _view(rid, tenant)["needed_by"] if not n.get("missing")]


def _uncertain_twice(sid) -> dict:
    """`sid` evaluated for F at NOW and 30 days ago, both UNCERTAIN: one OPEN request linking both."""
    for at in (NOW, ago(30)):
        assert _evaluate(sid, at)["outcome"] == "UNCERTAIN"
    req = _only_request(sid)
    assert req["status"] == "OPEN" and len(req["linked"]) == 2, req
    return req


# ------------------------------------------------------------------------------------------- unknown where needed
def test_an_assertion_that_ends_before_a_needed_instant_leaves_the_request_open(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    out = _write(sid, ago(400), ago(10))  # known 30 days ago, not today
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["UNCERTAIN"]
    after = _only_request(sid)
    assert (after["request_id"], after["status"], after["resolution"]) == (req["request_id"], "OPEN", None)
    assert not all(_needed_known(req["request_id"]))


def test_an_assertion_valid_only_in_the_future_leaves_the_request_open(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    _write(sid, ahead(100))
    after = _only_request(sid)
    assert (after["status"], after["resolution"]) == ("OPEN", None)
    assert not any(_needed_known(req["request_id"]))


def test_an_assertion_known_at_only_some_needed_instants_leaves_the_request_open(world):
    """Known today (the re-run APPLIES) but not 30 days ago, which a linked evaluation still needs."""
    sid = _subject()
    req = _uncertain_twice(sid)
    out = _write(sid, ago(20))
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["APPLIES"]
    assert _only_request(sid)["status"] == "OPEN"
    assert sorted(_needed_known(req["request_id"])) == [False, True]


def test_a_withdrawal_leaves_the_request_open(world):
    sid = _subject()
    _write(sid, ago(400), ago(60))  # known only long ago
    assert _evaluate(sid, NOW)["outcome"] == "UNCERTAIN"
    req = _only_request(sid)
    _write(sid, ago(400), ago(60), change_kind="WITHDRAWAL")
    after = _only_request(sid)
    assert (after["request_id"], after["status"], after["resolution"]) == (req["request_id"], "OPEN", None)


# ---------------------------------------------------------------------------------------------- known where needed
def test_an_assertion_known_at_every_needed_instant_resolves_the_request(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    out = _write(sid, ago(400))
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["APPLIES"]
    after = _only_request(sid)
    assert (after["request_id"], after["status"], after["resolution"]) == (req["request_id"], "RESOLVED", RESOLVED_TEXT)
    assert _needed_known(req["request_id"]) == [True, True]


def test_a_correction_that_supersedes_a_narrower_assertion_resolves_the_request(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    _write(sid, ago(20))
    assert _only_request(sid)["status"] == "OPEN"
    _write(sid, ago(400), change_kind="RETROACTIVE_CORRECTION")  # supersedes the narrower revision
    after = _only_request(sid)
    assert (after["request_id"], after["status"]) == (req["request_id"], "RESOLVED")
    assert _needed_known(req["request_id"]) == [True, True]


# --------------------------------------------------------------------------------------------- conflict and expiry
def _conflicted(sid) -> dict:
    """`sid` holds two disagreeing current F assertions from 400 days ago; evaluated today: a FACT_CONFLICT request."""
    _write(sid, ago(400))
    _second_source(sid, F, False)
    assert _evaluate(sid, NOW)["outcome"] == "UNCERTAIN"
    req = _only_request(sid)
    assert (req["status"], req["reason_code"]) == ("OPEN", "FACT_CONFLICT"), req
    return req


def test_a_write_that_leaves_a_conflict_in_place_keeps_the_request_open(world):
    sid = _subject()
    req = _conflicted(sid)
    out = _write(sid, ago(1000), ago(900))  # does not overlap the disagreeing assertions
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["UNCERTAIN"]
    after = _only_request(sid)
    assert after["status"] == "OPEN" and len(after["linked"]) == 2  # today's re-run links to the same open request
    assert [(n["current_resolution"]["known"], n["current_resolution"]["reason"])
            for n in _view(req["request_id"])["needed_by"]] == [(False, "FACT_CONFLICT")] * 2


def test_a_write_that_settles_a_conflict_resolves_the_request(world):
    sid = _subject()
    req = _conflicted(sid)
    out = _write(sid, ago(400))  # closes both overlapping assertions: one current value
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["APPLIES"]
    after = _only_request(sid)
    assert (after["request_id"], after["status"]) == (req["request_id"], "RESOLVED")


def test_an_expired_value_does_not_resolve_the_request(world):
    """FX expires one day after it is recorded. The request is needed ten days ahead, where the value written today
    has expired: it stays open, although today's re-run finds the fact."""
    sid = _subject()
    assert _evaluate(sid, ahead(10), key=FX)["outcome"] == "UNCERTAIN"
    req = _only_request(sid, FX)
    out = _write(sid, ago(400), key=FX)
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["APPLIES"]
    assert _only_request(sid, FX)["status"] == "OPEN"
    [needed] = _view(req["request_id"])["needed_by"]
    assert (needed["current_resolution"]["known"], needed["current_resolution"]["reason"]) == (False, "FACT_EXPIRED")


# --------------------------------------------------------------------------------------------------- the lifecycle
def test_a_request_whose_linked_evaluations_cannot_all_be_found_stays_open(world):
    """A link to no evaluation, or to another organisation's, cannot be shown to be satisfied; neither can a request
    that links none."""
    other = _subject(TB)
    foreign = _evaluate(other, NOW, tenant=TB)["evaluation_id"]
    for case in ("no-such-evaluation", foreign, None):
        sid = _subject()
        assert _evaluate(sid, NOW)["outcome"] == "UNCERTAIN"
        req = _only_request(sid)
        _set_request(req["request_id"], linked_evaluation_ids=[] if case is None else [*req["linked"], case])
        _write(sid, ago(400))
        assert _only_request(sid)["status"] == "OPEN", case


def test_a_resolved_request_stays_resolved_and_a_new_need_gets_its_own_open_request(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    _write(sid, ago(400))
    assert _only_request(sid)["status"] == "RESOLVED"
    out = _write(sid, ago(400), change_kind="WITHDRAWAL")  # the fact is unknown again; today's re-run needs it
    assert [x["outcome"] for x in out["impact_reevaluations"]] == ["UNCERTAIN"]
    first, second = _requests(sid)
    assert (first["request_id"], first["status"], first["resolution"]) == (req["request_id"], "RESOLVED", RESOLVED_TEXT)
    assert second["status"] == "OPEN" and second["resolution"] is None and len(second["linked"]) == 1


def test_an_in_progress_request_follows_the_same_rule(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    _set_request(req["request_id"], status="IN_PROGRESS")
    _write(sid, ago(20))
    assert _only_request(sid)["status"] == "IN_PROGRESS"
    _write(sid, ago(400), change_kind="RETROACTIVE_CORRECTION")
    assert _only_request(sid)["status"] == "RESOLVED"


def test_repeating_writes_changes_nothing_further(world):
    sid = _subject()
    req = _uncertain_twice(sid)
    for _ in range(2):
        _write(sid, ago(20))
        assert _only_request(sid) == {**req, "linked": _only_request(sid)["linked"]}
    for _ in range(2):
        _write(sid, ago(400), change_kind="RETROACTIVE_CORRECTION")
        after = _only_request(sid)
        assert (after["request_id"], after["status"], after["resolution"]) == (req["request_id"], "RESOLVED",
                                                                              RESOLVED_TEXT)


def test_another_organisations_requests_are_untouched(world):
    a, b = _subject(TA), _subject(TB)
    assert _evaluate(a, NOW)["outcome"] == "UNCERTAIN"
    assert _evaluate(b, NOW, tenant=TB)["outcome"] == "UNCERTAIN"
    _write(a, ago(400))
    assert _only_request(a)["status"] == "RESOLVED"
    assert _only_request(b)["status"] == "OPEN"
    _write(b, ago(20), ago(10), tenant=TB)  # B's own write that leaves today unknown
    assert _only_request(b)["status"] == "OPEN"
    _write(b, ago(400), tenant=TB)
    assert _only_request(b)["status"] == "RESOLVED"
