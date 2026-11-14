"""Regulatory catalogue and obligation-definition versioning integrity, within the approved model.

Definition versioning itself (a revision identity for released definitions, B1-a..B7 in
docs/obligation-definition-versioning-architecture.md) is OPEN and needs a migration; nothing here models it. These tests
prove what the existing model must already guarantee, and the one confirmed defect fixed in this change:

  * a released definition and the content it was released on cannot change in place (DAT-REQ-012, DOM §1.1/§6.1,
    RUL §12; engines/catalog.py _require_unreleased, revise_provision);
  * an adopted obligation keeps the exact rule version and requirement versions it was decided on, also after newer
    content is published (DAT-REQ-026, DOM §13, TIM-REQ-015; RELEASE_BOUND traces);
  * the catalogue names the rule version in force accurately: the latest PUBLISHED version is not reported as the
    current, binding one while it is not yet effective (DOM §13 "exactly one released rule version is effective for a
    valid instant"; TIM-REQ-026) -- the defect fixed here (effective_status / effective_rule_version);
  * a link edit racing a publication is serialised and refused once the release is committed;
  * historical and trace reads write nothing; obligations stay in their organisation (DOM-INV-11).
"""
from __future__ import annotations

import secrets
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"CVI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-cvia-{TAG.lower()}", f"tenant-cvib-{TAG.lower()}"
CCO, CCO_B = f"cvi-cco-{TAG.lower()}", f"cvib-cco-{TAG.lower()}"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


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


def _draft(client, tag: str, odef: str, rv: dict, valid_from="2025-01-01"):
    p = rv["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": rv["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
        "valid_from": valid_from, "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, r.text
    return did


def _publish(client, did):
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"cvi-{did[:8]}", "changelog": "probe"})
    assert r.status_code == 200, r.text
    return r.json()


def _requirement(client, key, valid_from="2025-01-01"):
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
        "title": "Register requirement", "locator": "Art. 1", "valid_from": valid_from, "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    return r.json()


def _definition(rv: dict, suffix: str) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"CVI_{TAG}_{suffix}", title=f"Register obligation {suffix}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _subject(name: str, tenant=TA) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=tenant, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    return sid


def _adopt(client, name, odef, tenant=TA, actor=CCO) -> dict:
    sid = _subject(name, tenant)
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(actor),
                        json={"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=40)).isoformat()})
        assert r.status_code == 200, r.text
    ev = client.post("/api/applicability/evaluations", headers=H(actor), json={"subject_id": sid, "obligation_definition_id": odef})
    assert ev.status_code == 200, ev.text
    lineage = ev.json()["current_determination"]["lineage_id"]
    a = client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(actor),
                    json={"decision": "ADOPT", "authority_basis": "Board CVI"})
    assert a.status_code == 200, a.text
    return {"sid": sid, "evaluation": ev.json(), "obligation": a.json()["obligation"]}


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Versioning Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="CVI CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="CVI CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Versioning Act",
        "locator": f"OJ L {TAG}/cvi", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    S["rv"] = _requirement(client, f"CVI-{TAG}-REQ")
    S["odef"] = _definition(S["rv"], "A")
    S["r1"] = _publish(client, _draft(client, "a1", S["odef"], S["rv"]))["rule_version"]
    S["adopted"] = _adopt(client, "adopted", S["odef"])
    return True


# ------------------------------------------------------------------------------------------------ released content
def test_a_released_definition_and_its_content_cannot_change_in_place(client, world):
    from app.db import AdminSession
    from app.models import AuditEvent
    other = _requirement(client, f"CVI-{TAG}-OTHER")
    before = client.get(f"/api/catalog/obligation-definitions/{S['odef']}", headers=H(AUTHOR)).json()
    with AdminSession() as db:
        audits = db.execute(select(func.count()).select_from(AuditEvent)).scalar()
    seq = _seq()
    p = S["rv"]["provisions"][0]
    attempts = [
        client.post(f"/api/catalog/obligation-definitions/{S['odef']}/links", headers=H(AUTHOR), json={
            "requirement_version_id": other["requirement_version_id"],
            "provision_id": other["provisions"][0]["provision_id"], "reason": "add after release"}),
        client.post(f"/api/catalog/obligation-definitions/{S['odef']}/links/{p['provision_id']}/remove",
                    headers=H(AUTHOR), json={"reason": "remove after release"}),
        client.put(f"/api/catalog/provisions/{p['provision_id']}", headers=H(AUTHOR), json={
            "duty_bearer": "FINANCIAL_ENTITY", "modality": "MAY", "action": "keep register", "regulated_object": "register",
            "normalized_meaning": "Weakened after release."})]
    assert [(r.status_code, r.json()["detail"]["error"]) for r in attempts] == [
        (409, "OBLIGATION_DEFINITION_RELEASED"), (409, "OBLIGATION_DEFINITION_RELEASED"), (409, "PROVISION_NOT_EDITABLE")]
    after = client.get(f"/api/catalog/obligation-definitions/{S['odef']}", headers=H(AUTHOR)).json()
    assert after["provision_ids"] == before["provision_ids"] and after["links"] == before["links"]
    with AdminSession() as db:
        assert db.execute(select(func.count()).select_from(AuditEvent)).scalar() == audits
    assert _seq() == seq


# ------------------------------------------------------------------------------------------------ in-force rule version
def test_a_future_effective_version_is_never_reported_as_the_rule_in_force(client, world):
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ObligationDefinition
    future = (date.today() + timedelta(days=400)).isoformat()
    r2 = _publish(client, _draft(client, "a2", S["odef"], S["rv"], valid_from=future))["rule_version"]
    assert r2["version"] > S["r1"]["version"]
    with AdminSession() as db:  # what applicability binds today
        bound = appl.resolve_rule(db, db.get(ObligationDefinition, S["odef"]), date.today(), _seq())
        assert bound.rule_version_id == S["r1"]["rule_version_id"]
    view = client.get(f"/api/catalog/obligation-definitions/{S['odef']}", headers=H(AUTHOR)).json()
    listed = next(d for d in client.get("/api/catalog/obligation-definitions", headers=H(AUTHOR)).json()
                  if d["obligation_definition_id"] == S["odef"])
    trace = client.get(f"/api/catalog/obligation-definitions/{S['odef']}/trace", headers=H(AUTHOR)).json()
    reverse = client.get(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/trace", headers=H(AUTHOR)).json()
    [down] = [d for d in reverse["obligation_definitions"] if d["obligation_definition_id"] == S["odef"]]
    # The latest published version keeps its place as the head of the lineage, and says it is not in force yet.
    for head in (view["current_rule_version"], listed["current_rule_version"], down["current_rule_version"]):
        assert (head["rule_version_id"], head["effective_status"]) == (r2["rule_version_id"], "NOT_YET_EFFECTIVE")
    assert (trace["current_rule"]["rule_version_id"], trace["current_rule"]["effective_status"]) == \
        (r2["rule_version_id"], "NOT_YET_EFFECTIVE")
    # The version in force is named explicitly and is the one applicability binds: forward and reverse traces agree.
    for effective in (view["effective_rule_version"], listed["effective_rule_version"], trace["effective_rule_version"],
                      down["effective_rule_version"]):
        assert effective["rule_version_id"] == S["r1"]["rule_version_id"] == bound.rule_version_id
        assert effective["effective_on"] == date.today().isoformat()


# ------------------------------------------------------------------------------------------------ historical provenance
def test_an_adopted_obligation_keeps_the_exact_versions_it_was_decided_on(client, world):
    lineage = S["adopted"]["obligation"]["lineage_id"]
    adopted_at = S["adopted"]["obligation"]["record_from_seq"]
    # Newer content after adoption: another requirement version of the same key is published by another definition,
    # superseding the one this obligation's release bound.
    newer = _requirement(client, f"CVI-{TAG}-REQ", valid_from="2026-01-01")
    odef_b = _definition(newer, "B")
    _publish(client, _draft(client, "b1", odef_b, newer))
    old_rv = client.get(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}", headers=H(AUTHOR)).json()
    assert old_rv["lifecycle"] == "SUPERSEDED"
    seq = _seq()
    for as_of in (None, adopted_at):
        params = {} if as_of is None else {"as_of_record_seq": as_of}
        d = client.get(f"/api/obligations/{lineage}", headers=H(CCO), params=params).json()
        prov = d["regulatory_provenance"]
        assert prov["binding_basis"] == "RELEASE_BOUND" and prov["rule_version_id"] == S["r1"]["rule_version_id"]
        assert [r["requirement"]["requirement_version_id"] for r in prov["requirements"]] == [S["rv"]["requirement_version_id"]]
        assert all(r["content_matches_binding"] is True for r in prov["requirements"])
        assert prov["integrity"]["status"] == "INTACT", prov["integrity"]
        assert d["obligation"]["obligation_definition_id"] == S["odef"]
    # The reverse trace of the superseded version still reaches the definition and the release that bound it.
    reverse = client.get(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/trace", headers=H(AUTHOR)).json()
    [down] = [x for x in reverse["obligation_definitions"] if x["obligation_definition_id"] == S["odef"]]
    assert S["rv"]["requirement_version_id"] in down["current_rule_version"]["release_binds"]
    assert down["current_rule_version"]["links_match_release"] is True
    assert _seq() == seq  # dossier and trace reads wrote nothing


def test_obligations_stay_in_their_organisation(client, world):
    lineage = S["adopted"]["obligation"]["lineage_id"]
    assert lineage not in [o["lineage_id"] for o in client.get("/api/obligations", headers=H(CCO_B)).json()]
    for params in ({}, {"as_of_record_seq": _seq()}):
        assert client.get(f"/api/obligations/{lineage}", headers=H(CCO_B), params=params).status_code in (403, 404)


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and locks; exact reason required by the CI gate
    return True


def test_a_link_edit_racing_a_publication_waits_and_is_refused(client, world, pg, monkeypatch):
    """Publication holds the definition row lock from before it reads the links until it commits; a link edit that
    starts meanwhile waits on that lock, then sees the release and is refused, so the release signs exactly the links
    that were committed before it."""
    from app.db import SessionLocal, set_principal
    from app.engines import catalog
    from app.models import Actor
    from app.routers.rules import PublishIn, publish
    from app.security import Ctx
    rv = _requirement(client, f"CVI-{TAG}-RACE")
    extra = _requirement(client, f"CVI-{TAG}-RACE2")
    odef = _definition(rv, "RACE")
    did = _draft(client, "race", odef, rv)
    held, release, out = threading.Event(), threading.Event(), {}
    real = catalog.lock_release_content

    def holding(*a, **k):
        r = real(*a, **k)
        if threading.current_thread().name == "publisher":
            held.set()
            assert release.wait(30)
        return r
    monkeypatch.setattr(catalog, "lock_release_content", holding)

    def run(name, fn):
        try:
            out[name] = ("ok", fn())
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)

    def do_publish():
        with SessionLocal() as db:
            set_principal(db, None, RELEASE)
            return publish(did, PublishIn(label=f"cvi-race-{TAG}", changelog="race"),
                           Ctx(db=db, actor=db.get(Actor, RELEASE)))["rule_version"]["rule_version_id"]

    def do_link():
        with SessionLocal() as db:
            set_principal(db, None, AUTHOR)
            catalog.add_link(Ctx(db=db, actor=db.get(Actor, AUTHOR)), odef, extra["requirement_version_id"],
                             extra["provisions"][0]["provision_id"], "race")
            db.commit()
    a = threading.Thread(target=run, args=("publish", do_publish), name="publisher")
    a.start()
    assert held.wait(60), out
    b = threading.Thread(target=run, args=("link", do_link), name="linker")
    b.start()
    b.join(2.0)
    waited = b.is_alive()
    release.set()
    a.join(90)
    b.join(90)
    assert waited and out["publish"][0] == "ok", out
    assert out["link"][0] == 409 and out["link"][1]["error"] == "OBLIGATION_DEFINITION_RELEASED", out
    view = client.get(f"/api/catalog/obligation-definitions/{odef}", headers=H(AUTHOR)).json()
    assert view["provision_ids"] == [p["provision_id"] for p in rv["provisions"]]
