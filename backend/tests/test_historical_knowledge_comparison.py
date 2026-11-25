"""GET /history/compare: what changed between two record cutoffs for the same valid time (ZA-TIM-001 §12 "What changed
between those answers? Same valid time, compare two record cutoffs", "Correction/source-late-arrival explanation").

Controlling text: ZA-TIM-001 §12 "Difference contract", whose classes describe what a later record did:
  KNOWLEDGE_ADDED        "A later record introduced previously unknown valid-time information."
  KNOWLEDGE_CORRECTED    "Later record corrected a prior assertion."
  LEGAL_VERSION_CHANGED  "Later content interpretation/rule release changed current reconstruction." (from -> to)
  NO_SEMANTIC_CHANGE     "Metadata/record additions do not alter reconstructed conclusion."
and ZA-APP-001 §03 (the knowledge an evaluation rests on is each required fact's resolution at its record cutoff: the
assertion revision, or why there is none; engines/facts.resolve_fact, frozen as fact_inputs / unknown_facts).

Defects fixed (both reproduced on the unchanged code by the tests marked below):
  1. A fact one side's rule path did not consult (a sibling already decided an ALL) was compared as if its knowledge had
     changed: "absent from this trace" was read as a revision change, so a correction that short-circuits the rule
     reported KNOWLEDGE_ADDED for facts nobody recorded anything about. Facts are now compared by what was known at
     each cutoff, for every fact either answer consulted.
  2. The classes were assigned from the left side to the right side whatever their record order, so with the later
     cutoff on the left, knowledge recorded later was called a correction, a withdrawal was called added knowledge and
     LEGAL_VERSION_CHANGED ran from the newer version to the older. Classes are now assigned from the earlier cutoff to
     the later one; the two sides are still returned exactly as requested.
Read-only: nothing is evaluated into storage, no fact conflict is recorded, no sequence is taken. Runs on SQLite and on
PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"HKC-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-hkca-{TAG.lower()}", f"tenant-hkcb-{TAG.lower()}"
CCO, CCO_B = f"hkc-cco-{TAG.lower()}", f"hkcb-cco-{TAG.lower()}"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
NOW = datetime.now(timezone.utc)
S: dict = {}


# ------------------------------------------------------------------------------------------------ world
def _ast(t: str) -> dict:
    """ALL(established in the EU, credit institution): a FALSE first child decides the ALL, so the second is skipped."""
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
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"HKC_{TAG}_{name}", title=f"Comparison obligation {name}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _publish(client, odef: str) -> str:
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
                    json={"label": f"hkc-{secrets.token_hex(4)}", "changelog": "hkc"})
    assert r.status_code == 200, r.text
    from app.db import AdminSession
    from app.models import RuleVersion
    with AdminSession() as db:
        return db.execute(select(RuleVersion.rule_version_id).where(RuleVersion.obligation_definition_id == odef)
                          .order_by(RuleVersion.version.desc())).scalars().first()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Comparison Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tenant in (TA, TB):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="HKC CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="HKC B CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Comparison Act",
        "locator": f"OJ L {TAG}/hkc", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"HKC-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code == 201, rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["odef"], S["odef_versions"] = _definition("A"), _definition("B")
    S["before_release"] = _seq()
    S["rv1"] = _publish(client, S["odef"])
    S["versions_v1"] = _publish(client, S["odef_versions"])
    return True


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _subject(name: str, tenant: str = TA) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=tenant, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    return sid


def _fact(sid, key, value, *, change_kind="CREATE", actor=CCO) -> str:
    """Record a fact over the last 40 days onwards; returns the new revision's ID (None for a withdrawal)."""
    body = {"fact_key": key, "value": value, "valid_from": (NOW - timedelta(days=40)).isoformat(),
            "change_kind": change_kind}
    if change_kind != "CREATE":
        body["reason"] = f"{change_kind.lower()} in test"
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(actor), json=body)
    assert r.status_code == 200, r.text
    new = [x["revision_id"] for x in r.json()["revisions"] if x["record_to_seq"] is None
           and x.get("change_kind") != "SPLIT"]
    return new[-1] if new else None


def _compare(sid, left, right, odef=None, actor=CCO):
    return S["client"].get("/api/history/compare", headers=H(actor), params={
        "subject_id": sid, "obligation_definition_id": odef or S["odef"], "valid_at": NOW.isoformat(),
        "left_seq": left, "right_seq": right})


def _ok(sid, left, right, odef=None) -> dict:
    r = _compare(sid, left, right, odef)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ defect 1
def test_a_fact_one_answer_did_not_consult_is_not_a_change_of_knowledge(client, world):
    """Established in the EU is recorded true and the entity type is never recorded (UNCERTAIN: both facts consulted).
    The EU fact is then corrected to false: the ALL is decided by its first child and the entity type is skipped
    (DOES_NOT_APPLY). Nothing was ever recorded about the entity type, so the only difference is the correction; the
    old comparison also reported KNOWLEDGE_ADDED for the entity type (left UNKNOWN, right null), in both orders."""
    sid = _subject("skip")
    first = _fact(sid, EU, True)
    s1 = _seq()
    corrected = _fact(sid, EU, False, change_kind="RETROACTIVE_CORRECTION")
    s2 = _seq()
    forward = _ok(sid, s1, s2)
    assert (forward["left"]["outcome"], forward["right"]["outcome"]) == ("UNCERTAIN", "DOES_NOT_APPLY")
    assert forward["differences"] == [{"type": "KNOWLEDGE_CORRECTED", "fact": EU, "left": first, "right": corrected}]
    backward = _ok(sid, s2, s1)
    assert backward["differences"] == [{"type": "KNOWLEDGE_CORRECTED", "fact": EU, "left": corrected, "right": first}]


# ------------------------------------------------------------------------------------------------ defect 2
def test_knowledge_recorded_later_is_added_whichever_side_it_is_on(client, world):
    """The EU fact is first recorded after the earlier cutoff: KNOWLEDGE_ADDED from the earlier answer to the later one.
    With the later cutoff on the left the old comparison called it KNOWLEDGE_CORRECTED."""
    sid = _subject("added")
    _fact(sid, KIND, "CREDIT_INSTITUTION")
    s0 = _seq()
    rev = _fact(sid, EU, True)
    s1 = _seq()
    forward = _ok(sid, s0, s1)
    assert (forward["left"]["outcome"], forward["right"]["outcome"]) == ("UNCERTAIN", "APPLIES")
    assert forward["differences"] == [{"type": "KNOWLEDGE_ADDED", "fact": EU, "left": "UNKNOWN", "right": rev}]
    backward = _ok(sid, s1, s0)
    assert (backward["left"]["known_seq"], backward["right"]["known_seq"]) == (s1, s0)  # sides as requested
    assert backward["differences"] == [{"type": "KNOWLEDGE_ADDED", "fact": EU, "left": rev, "right": "UNKNOWN"}]


def test_a_withdrawal_is_a_correction_whichever_side_it_is_on(client, world):
    """A later withdrawal corrects the prior assertion (it is not new knowledge). With the later cutoff on the left the
    old comparison called the withdrawal KNOWLEDGE_ADDED."""
    sid = _subject("withdrawn")
    _fact(sid, KIND, "CREDIT_INSTITUTION")
    rev = _fact(sid, EU, True)
    s1 = _seq()
    assert _fact(sid, EU, None, change_kind="WITHDRAWAL") is None
    s2 = _seq()
    forward = _ok(sid, s1, s2)
    assert (forward["left"]["outcome"], forward["right"]["outcome"]) == ("APPLIES", "UNCERTAIN")
    assert forward["differences"] == [{"type": "KNOWLEDGE_CORRECTED", "fact": EU, "left": rev, "right": "UNKNOWN"}]
    assert _ok(sid, s2, s1)["differences"] == [{"type": "KNOWLEDGE_CORRECTED", "fact": EU, "left": "UNKNOWN",
                                                "right": rev}]


def test_a_rule_release_is_reported_from_the_earlier_version_to_the_later(client, world):
    """A second rule version is released between the cutoffs: LEGAL_VERSION_CHANGED from the version the earlier answer
    used to the one the later answer used, in both orders (the old comparison ran it from the newer to the older one when
    the later cutoff was on the left). The facts are unchanged, so nothing else differs."""
    sid = _subject("release")
    _fact(sid, EU, True)
    _fact(sid, KIND, "CREDIT_INSTITUTION")
    s1 = _seq()
    v2 = _publish(client, S["odef_versions"])
    s2 = _seq()
    expected = [{"type": "LEGAL_VERSION_CHANGED", "from": S["versions_v1"], "to": v2}]
    forward = _ok(sid, s1, s2, S["odef_versions"])
    assert (forward["left"]["rule_version_id"], forward["right"]["rule_version_id"]) == (S["versions_v1"], v2)
    assert forward["differences"] == expected
    backward = _ok(sid, s2, s1, S["odef_versions"])
    assert (backward["left"]["rule_version_id"], backward["right"]["rule_version_id"]) == (v2, S["versions_v1"])
    assert backward["differences"] == expected


# ------------------------------------------------------------------------------------------------ guards
def test_unchanged_knowledge_is_no_semantic_change(client, world):
    """The same cutoff on both sides, and two cutoffs between which only a fact the rule does not use changed."""
    sid = _subject("same")
    _fact(sid, EU, True)
    _fact(sid, KIND, "CREDIT_INSTITUTION")
    s1 = _seq()
    _fact(sid, "entity.microenterprise", True)  # not a fact of this rule
    s2 = _seq()
    for left, right in ((s1, s1), (s1, s2), (s2, s1)):
        assert _ok(sid, left, right)["differences"] == [{"type": "NO_SEMANTIC_CHANGE"}], (left, right)


def test_a_side_that_could_not_be_answered_is_not_compared(client, world):
    """Before the rule was released there is no answer (the side carries its error); nothing is classified."""
    sid = _subject("unreleased")
    _fact(sid, EU, True)
    s1 = _seq()
    body = _ok(sid, S["before_release"], s1)
    assert "error" in body["left"] and "outcome" not in body["left"] and body["right"]["outcome"] == "UNCERTAIN"
    assert body["differences"] == []


def test_the_comparison_is_read_only_and_deterministic(client, world):
    """Nothing is written (no evaluation, no fact conflict, no record sequence), and the same question gets the same
    answer, byte for byte, with differences in fact-key order. A fact only one answer consulted is still reported when
    what was known about it did change: the entity type is consulted at the earlier cutoff only (UNKNOWN there) and is
    recorded before the later one."""
    sid = _subject("readonly")
    eu = _fact(sid, EU, True)
    s1 = _seq()
    eu2 = _fact(sid, EU, False, change_kind="RETROACTIVE_CORRECTION")
    kind = _fact(sid, KIND, "PAYMENT_INSTITUTION")
    s2 = _seq()
    before = db_fingerprint()
    answers = [_compare(sid, a, b) for a, b in ((s1, s2), (s2, s1), (s1, s2))]
    assert db_fingerprint() == before
    assert all(a.status_code == 200 for a in answers) and answers[0].text == answers[2].text
    assert answers[0].json()["differences"] == [
        {"type": "KNOWLEDGE_CORRECTED", "fact": EU, "left": eu, "right": eu2},
        {"type": "KNOWLEDGE_ADDED", "fact": KIND, "left": "UNKNOWN", "right": kind}]
    assert answers[1].json()["differences"] == [
        {"type": "KNOWLEDGE_CORRECTED", "fact": EU, "left": eu2, "right": eu},
        {"type": "KNOWLEDGE_ADDED", "fact": KIND, "left": kind, "right": "UNKNOWN"}]


def test_another_organisation_cannot_compare_this_subject(client, world):
    """Cross-tenant: another organisation's principal gets 404 for this subject, whatever cutoffs it sends, and its own
    subject's answer never includes this organisation's facts."""
    sid = _subject("tenant")
    _fact(sid, EU, True)
    s1 = _seq()
    assert _compare(sid, s1, s1, actor=CCO_B).status_code == 404
    other = _subject("tenant-b", tenant=TB)
    body = S["client"].get("/api/history/compare", headers=H(CCO_B), params={
        "subject_id": other, "obligation_definition_id": S["odef"], "valid_at": NOW.isoformat(),
        "left_seq": s1, "right_seq": s1}).json()
    assert body["left"]["outcome"] == "UNCERTAIN" and body["differences"] == [{"type": "NO_SEMANTIC_CHANGE"}]
