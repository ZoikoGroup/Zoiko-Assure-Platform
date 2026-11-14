"""Effective rule-version selection: one answer for one effective date across applicability, catalogue views, traces,
publication events and the catalogue cache.

Controlling text:
  * ZA-DOM-001 §13: "exactly one released rule version is effective for a valid instant";
  * ZA-TIM-001 TIM-REQ-026 (future-effective PUBLISHED content does not bind before its valid time), TIM-REQ-004
    (time is server-authoritative), TIM-REQ-002 (half-open [valid_from, valid_to));
  * ZA-EVT-001 rule.superseded: "Prior RuleVersion no longer current for new binding evaluations"; ZA-RUL-001 §17
    rule.superseded "Old/new RuleVersion relationship and effective time";
  * DOM-INV-11 (organisation isolation).

The selection under test is engines/applicability.resolve_rule (what an evaluation binds) and its read-only mirror
engines/catalog.effective_rule_version (what the catalogue reports). Both convert an evaluation instant to a date in
UTC (effective_at.date()); whether a legal date should instead follow a jurisdiction calendar is an open TIM decision
(DOC-CON-003; "conversions require an explicit policy") and is not decided here. Overlapping valid periods resolve to
the highest version number in both, as resolve_rule always has; no new overlap or gap policy is introduced.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"RES-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-resa-{TAG.lower()}", f"tenant-resb-{TAG.lower()}"
CCO, CCO_B = f"res-cco-{TAG.lower()}", f"resb-cco-{TAG.lower()}"
T = datetime.now(timezone.utc).date()  # the UTC date applicability resolves "now" to
S: dict = {}


def day(n: int) -> date:
    return T + timedelta(days=n)


def _ast(tag: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}


def _vectors() -> list[dict]:
    facts = lambda eu, v: {"entity.established_in_eu": eu, "entity.financial_entity_type": v}  # noqa: E731
    return [{"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
             "facts": facts(True, "CREDIT_INSTITUTION"), "expected_outcome": "APPLIES"},
            {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
             "facts": facts(False, "CREDIT_INSTITUTION"), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
             "facts": facts(True, {"unknown": "MISSING_REQUIRED_FACT"}), "expected_outcome": "UNCERTAIN"}]


def _publish(client, odef: str, valid_from: date, valid_to: date | None = None) -> dict:
    """Author, review, shadow and publish one rule version of ``odef`` with the given valid period."""
    tag = f"v{secrets.token_hex(3)}"
    p = S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
        "valid_from": valid_from.isoformat(), "valid_to": valid_to.isoformat() if valid_to else None,
        "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, r.text
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"res-{tag}", "changelog": "probe"})
    assert r.status_code == 200, r.text
    return r.json()["rule_version"]


def _definition(suffix: str) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RES_{TAG}_{suffix}", title=f"Effective selection {suffix}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _subject(name: str, tenant=TA, actor=CCO) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=tenant, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        r = client_post(f"/api/subjects/{sid}/facts", actor,
                        {"fact_key": key, "value": value, "valid_from": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()})
        assert r.status_code == 200, r.text
    return sid


def client_post(path, actor, body):
    return S["client"].post(path, headers=H(actor), json=body)


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _superseded_events(odef: str) -> list:
    from app.db import AdminSession
    from app.models import OutboxEvent
    with AdminSession() as db:
        rows = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "rule.superseded")
                          .order_by(OutboxEvent.record_seq)).scalars().all()
        return [(e.aggregate_id, e.payload["superseded_by_rule_version_id"]) for e in rows
                if e.payload.get("obligation_definition_id") == odef]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Selection Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="RES CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="RES CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Selection Act",
        "locator": f"OJ L {TAG}/res", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RES-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    # Definition A: adjacent v1/v2, a gap, v3 in force now, v4 published ahead of its effective date.
    S["A"] = _definition("A")
    S["A_v"] = [_publish(client, S["A"], day(-400), day(-200)), _publish(client, S["A"], day(-200), day(-100)),
                _publish(client, S["A"], day(-50)), _publish(client, S["A"], day(100))]
    # Definition B: its only version has ended; nothing is in force today.
    S["B"] = _definition("B")
    S["B_v"] = [_publish(client, S["B"], day(-400), day(-10))]
    return True


def _resolved(odef: str, on: date):
    """(applicability's binding, the catalogue's effective version) for one date, as rule version IDs or None."""
    from fastapi import HTTPException

    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.engines import catalog
    from app.models import ObligationDefinition
    with AdminSession() as db:
        try:
            bound = appl.resolve_rule(db, db.get(ObligationDefinition, odef), on, _seq()).rule_version_id
        except HTTPException as e:
            assert e.status_code == 409 and e.detail["error"] == "RULE_NOT_EFFECTIVE"
            bound = None
        eff = catalog.effective_rule_version(db, odef, on)
        return bound, eff.rule_version_id if eff else None


# ------------------------------------------------------------------------------------------------ selection
def test_applicability_and_catalogue_select_the_same_version_at_every_boundary(client, world):
    v1, v2, v3, v4 = (v["rule_version_id"] for v in S["A_v"])
    cases = {day(-400): v1,               # exactly at v1.valid_from (inclusive)
             day(-201): v1, day(-200): v2,  # adjacent boundary: v1.valid_to (exclusive) == v2.valid_from (inclusive)
             day(-101): v2, day(-100): None,  # exactly at v2.valid_to: ended, and the gap has no version
             day(-60): None, day(-50): v3, T: v3,
             day(99): v3, day(100): v4}       # a future version takes over exactly at its valid_from
    for on, expected in cases.items():
        assert _resolved(S["A"], on) == (expected, expected), on
    assert _resolved(S["B"], T) == (None, None)


def test_an_explicit_past_effective_date_binds_the_version_in_force_then(client, world):
    sid = _subject("past")
    v2 = S["A_v"][1]["rule_version_id"]
    r = client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid, "obligation_definition_id": S["A"],
        "effective_at": datetime.combine(day(-150), datetime.min.time(), tzinfo=timezone.utc).isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["rule_version_id"] == v2 == _resolved(S["A"], day(-150))[1]
    gap = client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid, "obligation_definition_id": S["A"],
        "effective_at": datetime.combine(day(-60), datetime.min.time(), tzinfo=timezone.utc).isoformat()})
    assert gap.status_code == 409 and gap.json()["detail"]["error"] == "RULE_NOT_EFFECTIVE"
    now = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": S["B"]})
    assert now.status_code == 409 and now.json()["detail"]["error"] == "RULE_NOT_EFFECTIVE"


def test_catalogue_views_report_the_latest_and_the_effective_version_distinctly(client, world):
    from app.db import AdminSession
    from app.engines import catalog
    from app.models import RuleVersion
    v1, v2, v3, v4 = (v["rule_version_id"] for v in S["A_v"])
    view = client.get(f"/api/catalog/obligation-definitions/{S['A']}", headers=H(AUTHOR)).json()
    assert (view["current_rule_version"]["rule_version_id"], view["current_rule_version"]["effective_status"]) == \
        (v4, "NOT_YET_EFFECTIVE")
    eff = view["effective_rule_version"]
    assert (eff["rule_version_id"], eff["valid_from"], eff["valid_to"]) == (v3, day(-50).isoformat(), None)
    with AdminSession() as db:
        effective = db.get(RuleVersion, v3)
        assert [catalog.rule_effective_status(db.get(RuleVersion, v), effective) for v in (v1, v2, v3, v4)] == \
            ["NO_LONGER_EFFECTIVE", "NO_LONGER_EFFECTIVE", "EFFECTIVE", "NOT_YET_EFFECTIVE"]
    ended = client.get(f"/api/catalog/obligation-definitions/{S['B']}", headers=H(AUTHOR)).json()
    assert ended["effective_rule_version"] is None  # nothing in force: explicit, never the latest substituted
    assert ended["current_rule_version"]["effective_status"] == "NO_LONGER_EFFECTIVE" and ended["released"] is True


# ------------------------------------------------------------------------------------------------ rule.superseded
def test_rule_superseded_names_the_version_that_was_in_force(client, world):
    """A version published ahead of its effective date never became current, so a later version that takes effect
    now supersedes the one actually in force (EVT rule.superseded: "Prior RuleVersion no longer current for new binding
    evaluations"), not the latest-numbered one."""
    odef = _definition("C")
    v1 = _publish(client, odef, day(-400))
    future = _publish(client, odef, day(200))
    assert _superseded_events(odef) == []  # not in force: its predecessor stays current
    v3 = _publish(client, odef, day(-5))
    assert _superseded_events(odef) == [(v1["rule_version_id"], v3["rule_version_id"])], future["rule_version_id"]
    # A version whose valid period has already ended when published is not in force either: nothing is superseded.
    ended = _definition("C2")
    w1 = _publish(client, ended, day(-400))
    _publish(client, ended, day(-300), day(-200))
    assert _superseded_events(ended) == [] and _resolved(ended, T) == (w1["rule_version_id"],) * 2


def test_publication_decides_in_force_on_the_same_date_as_applicability(client, world, monkeypatch):
    """On a host whose local date is already tomorrow (east of UTC after local midnight), publication must not treat a
    version starting tomorrow as in force while every evaluation in the same request still binds its predecessor."""
    from app.routers import rules as rules_router

    class LocalTomorrow(date):
        @classmethod
        def today(cls):
            return T + timedelta(days=1)
    monkeypatch.setattr(rules_router, "date", LocalTomorrow)
    odef = _definition("D")
    v1 = _publish(client, odef, day(-400))
    tomorrow = _publish(client, odef, day(1))
    assert _superseded_events(odef) == []
    assert _resolved(odef, T) == (v1["rule_version_id"],) * 2
    view = client.get(f"/api/catalog/obligation-definitions/{odef}", headers=H(AUTHOR)).json()
    assert (view["current_rule_version"]["rule_version_id"], view["current_rule_version"]["effective_status"]) == \
        (tomorrow["rule_version_id"], "NOT_YET_EFFECTIVE")


# ------------------------------------------------------------------------------------------------ cache
def test_the_cached_list_is_keyed_by_effective_date_and_equals_the_uncached_one(client, world, monkeypatch):
    from app import redis_client
    from app.engines import catalog
    from test_redis_cache import Broken, MemoryRedis
    v3, v4 = S["A_v"][2]["rule_version_id"], S["A_v"][3]["rule_version_id"]

    def listed(on):
        monkeypatch.setattr(catalog, "_today", lambda: on)
        r = client.get("/api/catalog/obligation-definitions", headers=H(AUTHOR))
        assert r.status_code == 200, r.text
        return next(d for d in r.json() if d["obligation_definition_id"] == S["A"])
    monkeypatch.delenv("ZA_CATALOG_CACHE", raising=False)
    direct = {on: listed(on) for on in (T, day(100))}
    assert direct[T]["effective_rule_version"]["rule_version_id"] == v3
    assert direct[day(100)]["effective_rule_version"]["rule_version_id"] == v4
    assert direct[day(100)]["current_rule_version"]["effective_status"] == "EFFECTIVE"
    fake = MemoryRedis()
    redis_client.manager.use_client(fake)
    try:
        monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
        for on in (T, day(100), T):  # the second read of T is served from its own entry
            assert listed(on) == direct[on]
        keys = sorted(k for k in fake.keys("*") if ":catalog:obligation_definitions." in k)
        assert [k for k in keys if T.isoformat() in k] and [k for k in keys if day(100).isoformat() in k]
        redis_client.manager.use_client(Broken())  # Redis down: PostgreSQL answers, the same content
        assert listed(day(100)) == direct[day(100)]
    finally:
        redis_client.manager.close()


# ------------------------------------------------------------------------------------------------ isolation, read-only
def test_catalogue_reads_write_nothing_and_evaluations_stay_in_their_organisation(client, world):
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent
    sid = _subject("iso")
    ev = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": S["A"]})
    assert ev.status_code == 200 and ev.json()["rule_version_id"] == S["A_v"][2]["rule_version_id"]

    def counts():
        with AdminSession() as db:
            return (_seq(), *(db.execute(select(func.count()).select_from(m)).scalar() for m in (AuditEvent, OutboxEvent)))
    before = counts()
    for path in ("/api/catalog/obligation-definitions", f"/api/catalog/obligation-definitions/{S['A']}",
                 f"/api/catalog/obligation-definitions/{S['A']}/trace",
                 f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/trace"):
        assert client.get(path, headers=H(AUTHOR)).status_code == 200, path
    assert client.get(f"/api/applicability/evaluations/{ev.json()['evaluation_id']}", headers=H(CCO)).status_code == 200
    assert counts() == before
    assert client.get(f"/api/applicability/evaluations/{ev.json()['evaluation_id']}", headers=H(CCO_B)).status_code in (403, 404)
    assert ev.json()["evaluation_id"] not in [e["evaluation_id"] for e in client.get(
        "/api/applicability/evaluations", headers=H(CCO_B)).json()]
