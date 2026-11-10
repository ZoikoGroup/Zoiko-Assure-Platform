"""Regulatory requirement and traceability engine (docs/regulatory-requirement-traceability-implementation-report.md).

Requirements exercised: ZA-GLS-001 §04 content lifecycle; ZA-DOM-001 §4.1 (SourceSnapshot immutable, digest), §13
cardinalities, DOM-INV-01/04/13/15, §16 end-to-end trace; ZA-DAT-001 §05 (fingerprint + authoritative reference
before VERIFIED/PUBLISHED; requirement_version exact source fingerprint); ZA-ARCH-001 INV-06 ("FK + non-null release
gate") and INV-09 (AI content needs VERIFIED review); PRD FR-REG-001/002/005; ZA-RUL-001 §02 (release = signed package of
RequirementVersions), §08 (source mappings, coverage, orphans), §11 stage 2; ZA-API-001 API-REQ-012/014/023/033;
ZA-EVT-001 requirement.version_published / requirement.version_superseded.

Every test builds its own instrument, sources, requirement versions, obligation definition and probe organisations, so
the seeded Customer Zero catalogue is read but never changed.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint, denials

TAG = secrets.token_hex(3).upper()
INSTR, INSTR_B = f"RT-{TAG}-REG", f"RT-{TAG}-OTHER"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-rt-a-{TAG}", f"tenant-rt-b-{TAG}"
A_CCO, B_CCO, SUPER = f"rt-a-cco-{TAG}", f"rt-b-cco-{TAG}", f"rt-super-{TAG}"
SUBJECT = f"subj-rt-a-{TAG}"


def _audit(action, **where):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        q = select(AuditEvent).where(AuditEvent.action == action)
        for k, v in where.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return db.execute(q.order_by(AuditEvent.record_seq, AuditEvent.id)).scalars().all()


def _row(model, key):
    from app.db import AdminSession
    with AdminSession() as db:
        r = db.get(model, key)
        if r is not None:
            db.expunge(r)
        return r


@pytest.fixture(scope="module")
def world(client):
    """Two test instruments, two probe organisations (subject, CCO each) and a Zoiko Super Admin."""
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for ident in (INSTR, INSTR_B):
            db.add(Instrument(identifier=ident, title=f"Traceability probe instrument {ident}", jurisdiction="EU",
                              authority="Probe Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid, sid in ((TA, SUBJECT), (TB, f"subj-rt-b-{TAG}")):
            if db.get(Tenant, tid) is None:
                db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
                db.flush()
                db.add(LegalEntity(entity_id=f"entity-{tid}", tenant_id=tid, legal_name=f"{tid} Bank AG", country="DE",
                                   home_cell_id=CELL_ID, subject_id=sid))
                db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=f"entity-{tid}", subject_type="LEGAL_ENTITY",
                               display_name=f"{tid} Bank AG"))
        for aid, tid, roles in ((A_CCO, TA, ["CCO"]), (B_CCO, TB, ["CCO"]), (SUPER, None, ["SUPER_ADMIN"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _source(client, text, *, locator=None, instrument=INSTR, authority="Probe Authority", jurisdiction="EU", **extra):
    body = {"authority": authority, "jurisdiction": jurisdiction, "source_type": "PRIMARY_LEGISLATION",
            "title": "Probe regulation (synthetic)", "locator": locator or f"OJ L {secrets.token_hex(4)}",
            "raw_text": text, "instrument_identifier": instrument, **extra}
    return client.post("/api/catalog/sources", headers=H(AUTHOR), json=body)


PROVISIONS = [
    {"locator": "Art. 4(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain a register",
     "regulated_object": "ICT third-party arrangements", "timing": "continuous",
     "normalized_meaning": "Maintain a register of ICT third-party arrangements.", "evidence_expectation": "Register"},
    {"locator": "Art. 4(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "review the register",
     "regulated_object": "ICT third-party register", "timing": "at least annually",
     "normalized_meaning": "Review the register at least annually.", "evidence_expectation": "Review record"},
]


def _requirement(client, source_id, key, *, valid_from="2025-01-01", instrument=INSTR, locator="Art. 4", **extra):
    body = {"source_id": source_id, "instrument_identifier": instrument, "requirement_key": key,
            "title": "Register of ICT third-party arrangements", "locator": locator, "valid_from": valid_from,
            "provisions": PROVISIONS, **extra}
    return client.post("/api/catalog/requirements", headers=H(AUTHOR), json=body)


# ------------------------------------------------------------------------------------------------ Phase A: sources
def test_source_registration_validates_and_records_its_lifecycle(client, world):
    text = "Financial entities shall maintain a register. The register shall be reviewed."
    good = {"authority": "Probe Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
            "title": "Probe", "locator": "OJ L 1/1", "raw_text": text}
    for patch in ({"raw_text": ""}, {"raw_text": None}, {"authority": "x" * 201}, {"jurisdiction": "x" * 33},
                  {"locator": "x" * 501}, {"title": "x" * 301}, {"source_type": "x" * 49}, {"rights_class": "x" * 49},
                  {"fingerprint": "f" * 64}, {"lifecycle": "PUBLISHED"}, {"instrument_identifier": "x" * 121}):
        body = {k: v for k, v in {**good, **patch}.items() if v is not None}
        assert client.post("/api/catalog/sources", headers=H(AUTHOR), json=body).status_code == 422, patch
    assert _source(client, text, instrument="NO-SUCH-INSTRUMENT").status_code == 404
    unclassified = _source(client, text, instrument="")  # blank (the frontend form's default): not classified yet
    assert unclassified.status_code == 200 and unclassified.json()["lifecycle"] == "INGESTED"
    r = _source(client, "  " + text + "  ")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["lifecycle"] == "CLASSIFIED" and s["fingerprint"] == hashlib.sha256(text.encode()).hexdigest()
    assert [a.new_state for a in _audit("source.ingest", object_id=s["source_id"])] == ["INGESTED"]
    cls = _audit("source.classify", object_id=s["source_id"])
    assert [(a.prior_state, a.new_state, a.actor_id) for a in cls] == [("INGESTED", "CLASSIFIED", AUTHOR)]
    assert INSTR in cls[0].reason
    detail = client.get(f"/api/catalog/sources/{s['source_id']}", headers=H("actor-cco")).json()
    assert detail["fingerprint_intact"] is True and detail["requirement_versions"] == []


def test_a_source_snapshot_is_registered_once_and_changed_text_is_a_new_version(client, world):
    loc = f"OJ L {TAG}/snapshots"
    first = _source(client, "Entities shall keep logs.", locator=loc).json()
    dup = _source(client, "Entities shall keep logs.", locator=loc)
    assert dup.status_code == 409 and dup.json()["detail"]["error"] == "DUPLICATE_SOURCE_SNAPSHOT"
    assert dup.json()["detail"]["source_id"] == first["source_id"]
    second = _source(client, "Entities shall keep logs for five years.", locator=loc).json()
    v = client.get(f"/api/catalog/sources/{second['source_id']}/versions", headers=H("actor-cco")).json()
    assert v["source_identity"]["locator"] == loc
    assert [(x["source_id"], x["snapshot_number"], x["fingerprint_changed"]) for x in v["snapshots"]] == [
        (first["source_id"], 1, False), (second["source_id"], 2, True)]
    # The earlier snapshot is untouched: the same fingerprint over the same text.
    again = client.get(f"/api/catalog/sources/{first['source_id']}", headers=H(AUTHOR)).json()
    assert again["fingerprint"] == first["fingerprint"] and again["raw_text"] == "Entities shall keep logs."
    assert client.get("/api/catalog/sources/no-such/versions", headers=H("actor-cco")).status_code == 404


def test_classification_follows_the_content_lifecycle(client, world):
    s = _source(client, "Entities shall notify incidents.", instrument="").json()
    assert client.post(f"/api/catalog/sources/{s['source_id']}/classify", headers=H(AUTHOR),
                       json={"instrument_identifier": "NO-SUCH"}).status_code == 404
    ok = client.post(f"/api/catalog/sources/{s['source_id']}/classify", headers=H(AUTHOR), json={"instrument_identifier": INSTR})
    assert ok.status_code == 200 and ok.json()["lifecycle"] == "CLASSIFIED"
    again = client.post(f"/api/catalog/sources/{s['source_id']}/classify", headers=H(AUTHOR), json={"instrument_identifier": INSTR})
    assert again.status_code == 200 and len(_audit("source.classify", object_id=s["source_id"])) == 1  # idempotent
    assert _requirement(client, s["source_id"], f"RT-{TAG}-CLS").status_code == 201  # source -> REVIEW_REQUIRED
    late = client.post(f"/api/catalog/sources/{s['source_id']}/classify", headers=H(AUTHOR), json={"instrument_identifier": INSTR})
    assert late.status_code == 409 and late.json()["detail"]["error"] == "CONTENT_TRANSITION_NOT_ALLOWED"
    assert late.json()["detail"]["from"] == "REVIEW_REQUIRED" and "CLASSIFIED" not in late.json()["detail"]["allowed"]
    assert client.post("/api/catalog/sources/no-such/classify", headers=H(AUTHOR),
                       json={"instrument_identifier": INSTR}).status_code == 404


# ------------------------------------------------------------------------------------------------ Phase B: requirements
def test_extraction_needs_a_classified_source_and_stays_non_binding(client, world):
    raw = _source(client, "Firms shall test plans. Firms must not delay reports.", instrument="").json()
    body = {"instrument_identifier": INSTR, "requirement_key": f"RT-{TAG}-EXT", "requirement_title": "Testing",
            "locator": "Art. 9", "valid_from": "2025-01-01"}
    r = client.post(f"/api/catalog/sources/{raw['source_id']}/extract", headers=H(AUTHOR), json=body)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SOURCE_NOT_CLASSIFIED"
    s = _source(client, "Firms shall test plans. Firms must not delay reports.").json()
    r = client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    rv, props = r.json()["requirement_version"], r.json()["proposed_provisions"]
    assert rv["lifecycle"] == "REVIEW_REQUIRED" and rv["origin"] == "AI_PROPOSED"
    assert [(p["lifecycle"], p["semantic_origin"], p["modality"]) for p in props] == [
        ("AI_EXTRACTED", "AI_PROPOSED", "MUST"), ("AI_EXTRACTED", "AI_PROPOSED", "MUST_NOT")]
    assert client.get(f"/api/catalog/sources/{s['source_id']}", headers=H(AUTHOR)).json()["lifecycle"] == "REVIEW_REQUIRED"
    assert _audit("requirement.create", object_id=rv["requirement_version_id"])[0].new_state == "REVIEW_REQUIRED"
    for patch in ({"locator": "x" * 196}, {"requirement_key": "x" * 121}, {"requirement_title": "x" * 301}, {"extra": 1}):
        assert client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR),
                           json={**body, **patch}).status_code == 422, patch


def test_requirement_versions_are_validated_unique_and_never_overwritten(client, world):
    s = _source(client, "Financial entities shall maintain a register of ICT arrangements.").json()
    key = f"RT-{TAG}-REQ"
    r1 = _requirement(client, s["source_id"], key)
    assert r1.status_code == 201, r1.text
    v1 = r1.json()
    assert (v1["lifecycle"], v1["origin"], v1["version_number"]) == ("REVIEW_REQUIRED", "ZOIKO_ENCODED", 1)
    assert {p["lifecycle"] for p in v1["provisions"]} == {"REVIEW_REQUIRED"} and v1["source_id"] == s["source_id"]
    dup = _requirement(client, s["source_id"], key)
    assert dup.status_code == 409 and dup.json()["detail"]["error"] == "DUPLICATE_REQUIREMENT_VERSION"
    elsewhere = _requirement(client, s["source_id"], key, instrument=INSTR_B)
    assert elsewhere.status_code == 409 and elsewhere.json()["detail"]["error"] == "REQUIREMENT_KEY_CONFLICT"
    assert _requirement(client, "no-such-source", f"RT-{TAG}-X").status_code == 404
    assert _requirement(client, s["source_id"], f"RT-{TAG}-X", instrument="NO-SUCH").status_code == 404
    for patch in ({"provisions": []}, {"valid_to": "2024-12-31"}, {"valid_to": "2025-01-01"}, {"requirement_key": "x" * 121},
                  {"locator": "x" * 201}, {"lifecycle": "PUBLISHED"}, {"origin": "AI_PROPOSED"},
                  {"provisions": [{**PROVISIONS[0], "action": "x" * 121}]}, {"provisions": [{**PROVISIONS[0], "extra": 1}]},
                  {"provisions": [{**PROVISIONS[0], "lifecycle": "VERIFIED"}]}):
        assert _requirement(client, s["source_id"], f"RT-{TAG}-V", **patch).status_code == 422, patch
    v2 = _requirement(client, s["source_id"], key, valid_from="2026-01-01").json()
    assert v2["version_number"] == 2 and v2["requirement_version_id"] != v1["requirement_version_id"]
    before = {k: v for k, v in v1.items() if k not in ("provisions", "version_number")}
    after = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}", headers=H(AUTHOR)).json()
    assert {k: after[k] for k in before} == before and (after["version_number"], after["versions_total"]) == (1, 2)


def test_requirement_version_history_lists_every_version_and_its_recorded_changes(client, world):
    s = _source(client, "Entities shall appoint an officer.").json()
    key = f"RT-{TAG}-HIST"
    v1 = _requirement(client, s["source_id"], key).json()
    p = v1["provisions"][0]
    edit = client.put(f"/api/catalog/provisions/{p['provision_id']}", headers=H(AUTHOR), json={
        **{k: p[k] for k in ("duty_bearer", "modality", "regulated_object", "timing", "normalized_meaning",
                             "evidence_expectation")}, "action": "keep a register", "reason": "Clarified the verb"})
    assert edit.status_code == 200, edit.text
    v2 = _requirement(client, s["source_id"], key, valid_from="2027-01-01").json()
    staff = client.get(f"/api/catalog/requirements/{v2['requirement_version_id']}/history", headers=H(LEGAL)).json()
    assert [v["requirement_version_id"] for v in staff["versions"]] == [v1["requirement_version_id"], v2["requirement_version_id"]]
    assert [v["version_number"] for v in staff["versions"]] == [1, 2]
    assert all(v["source"]["fingerprint"] == s["fingerprint"] for v in staff["versions"])
    actions = [(e["action"], e["object_id"]) for e in staff["events"]]
    assert ("requirement.create", v1["requirement_version_id"]) in actions and ("provision.revise", p["provision_id"]) in actions
    revise = next(e for e in staff["events"] if e["action"] == "provision.revise")
    assert '"action": "maintain a register"' in revise["reason"] and "Clarified the verb" in revise["reason"]
    # Organisations read the versions but not Zoiko's internal content-operations records.
    tenant = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}/history", headers=H("actor-cco")).json()
    assert tenant["events"] is None and tenant["versions"] == staff["versions"]
    assert client.get("/api/catalog/requirements/no-such/history", headers=H(AUTHOR)).status_code == 404


def test_review_verification_and_ai_content_rules(client, world):
    s = _source(client, "Firms shall document plans. Firms shall approve plans.").json()
    ext = client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR), json={
        "instrument_identifier": INSTR, "requirement_key": f"RT-{TAG}-VER", "requirement_title": "Plans",
        "locator": "Art. 7", "valid_from": "2025-01-01"}).json()
    rv_id, props = ext["requirement_version"]["requirement_version_id"], ext["proposed_provisions"]
    blocked = client.post(f"/api/catalog/requirements/{rv_id}/verify", headers=H(LEGAL))
    assert blocked.status_code == 409 and blocked.json()["detail"]["error"] == "PROVISIONS_NOT_REVIEWED"  # INV-09
    for p in props:
        r = client.put(f"/api/catalog/provisions/{p['provision_id']}", headers=H(AUTHOR), json={
            "duty_bearer": "FIRM", "modality": "MUST", "action": p["action"][:100], "regulated_object": "plan",
            "normalized_meaning": p["normalized_meaning"]})
        assert r.status_code == 200 and r.json()["lifecycle"] == "REVIEW_REQUIRED" and r.json()["semantic_origin"] == "ZOIKO_ENCODED"
    for actor in (AUTHOR, RELEASE, "actor-cco"):
        assert client.post(f"/api/catalog/requirements/{rv_id}/verify", headers=H(actor)).status_code == 403, actor
    ok = client.post(f"/api/catalog/requirements/{rv_id}/verify", headers=H(LEGAL))
    assert ok.status_code == 200 and ok.json()["lifecycle"] == "VERIFIED" and ok.json()["reviewed_by"] == LEGAL
    assert {client.get(f"/api/catalog/requirements/{rv_id}", headers=H(AUTHOR)).json()["provisions"][i]["lifecycle"]
            for i in range(2)} == {"VERIFIED"}
    again = client.post(f"/api/catalog/requirements/{rv_id}/verify", headers=H(LEGAL))
    assert again.status_code == 409 and again.json()["detail"]["error"] == "CONTENT_TRANSITION_NOT_ALLOWED"
    frozen = client.put(f"/api/catalog/provisions/{props[0]['provision_id']}", headers=H(AUTHOR), json={
        "duty_bearer": "FIRM", "modality": "MUST", "action": "changed", "regulated_object": "plan", "normalized_meaning": "x" * 3})
    assert frozen.status_code == 409 and frozen.json()["detail"]["error"] == "PROVISION_NOT_EDITABLE"
    # A requirement with no provisions is never verified (DOM §13 RequirementVersion 1:N ProvisionVersion).
    plain = _source(client, "This annex describes the scope of the regulation.").json()
    empty = client.post(f"/api/catalog/sources/{plain['source_id']}/extract", headers=H(AUTHOR), json={
        "instrument_identifier": INSTR, "requirement_key": f"RT-{TAG}-EMPTY", "requirement_title": "Scope",
        "locator": "Annex I", "valid_from": "2025-01-01"}).json()
    r = client.post(f"/api/catalog/requirements/{empty['requirement_version']['requirement_version_id']}/verify", headers=H(LEGAL))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "REQUIREMENT_HAS_NO_PROVISIONS"


def test_requirement_listing_filters_and_cursor_pagination(client, world):
    s = _source(client, "Entities shall publish a policy.", instrument=INSTR_B).json()
    keys = [f"RT-{TAG}-L{i}" for i in range(5)]
    for k in keys:
        assert _requirement(client, s["source_id"], k, instrument=INSTR_B).status_code == 201
    seen, cursor, pages = [], None, 0
    while True:
        params = {"instrument": INSTR_B, "limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/catalog/requirements", params=params, headers=H("actor-cco")).json()
        pages += 1
        seen += body["items"]
        cursor = body["page"]["next_cursor"]
        if not body["page"]["has_more"]:
            break
    assert pages == 3 and [i["requirement_key"] for i in seen] == sorted(keys)
    assert all(i["instrument"]["identifier"] == INSTR_B and i["provision_count"] == 2 for i in seen)
    assert all(i["source"]["fingerprint"] == s["fingerprint"] for i in seen)

    def keys_for(**params):
        r = client.get("/api/catalog/requirements", params={"limit": 200, **params}, headers=H(AUTHOR))
        assert r.status_code == 200, r.text
        return [i["requirement_key"] for i in r.json()["items"]]
    assert keys_for(requirement_key=keys[0]) == [keys[0]]
    assert set(keys) <= set(keys_for(source_id=s["source_id"], lifecycle="REVIEW_REQUIRED", origin="ZOIKO_ENCODED"))
    assert not set(keys) & set(keys_for(lifecycle="PUBLISHED"))
    assert set(keys) <= set(keys_for(jurisdiction="EU", valid_at="2025-06-01")) and not set(keys) & set(keys_for(valid_at="2024-06-01"))
    first = client.get("/api/catalog/requirements", params={"instrument": INSTR_B, "limit": 2}, headers=H(AUTHOR)).json()
    cur = first["page"]["next_cursor"]
    for params in ({"instrument": INSTR, "limit": 2, "cursor": cur}, {"instrument": INSTR_B, "limit": 3, "cursor": cur},
                   {"instrument": INSTR_B, "limit": 2, "cursor": cur[:-2] + ("BB" if cur.endswith("AA") else "AA")}):
        r = client.get("/api/catalog/requirements", params=params, headers=H(AUTHOR))
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor", params
    for params in ({"lifecycle": "APPROVED"}, {"limit": 0}, {"limit": 201}, {"origin": "ai"}, {"valid_at": "soon"}):
        assert client.get("/api/catalog/requirements", params=params, headers=H(AUTHOR)).status_code == 422, params
    assert client.get("/api/catalog/requirements").status_code == 401
    assert client.get("/api/catalog/requirements", headers=H(SUPER)).status_code == 403  # no catalogue read


# ------------------------------------------------------------------------------------------------ Phase C: traceability
@pytest.fixture(scope="module")
def chain(client, world):
    """A source snapshot -> requirement version (two provisions) -> obligation definition linking both provisions."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    s = _source(client, "Financial entities shall maintain a register of ICT third-party arrangements and review it.",
                locator=f"OJ L {TAG}/chain").json()
    v1 = _requirement(client, s["source_id"], f"RT-{TAG}-CHAIN").json()
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RT_{TAG}_REGISTER", title="Maintain the ICT third-party register (probe)",
                                 instrument_id=inst.instrument_id, provision_ids=[p["provision_id"] for p in v1["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        odef_id = o.obligation_definition_id
    return {"source": s, "v1": v1, "odef_id": odef_id}


def test_forward_and_reverse_traceability(client, chain):
    s, v1, odef_id = chain["source"], chain["v1"], chain["odef_id"]
    fwd = client.get(f"/api/catalog/sources/{s['source_id']}/trace", headers=H("actor-cco")).json()
    [req] = fwd["requirements"]
    assert req["requirement"]["requirement_version_id"] == v1["requirement_version_id"]
    assert {p["provision_id"] for p in req["provisions"]} == {p["provision_id"] for p in v1["provisions"]}
    assert [o["obligation_definition_id"] for o in req["obligation_definitions"]] == [odef_id]
    assert req["obligation_definitions"][0]["current_rule_version"] is None  # not released yet
    rev = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}/trace", headers=H("actor-ext-auditor")).json()
    assert rev["source"]["source_id"] == s["source_id"] and rev["source"]["fingerprint"] == s["fingerprint"]
    assert rev["source"]["fingerprint_intact"] is True and "raw_text" not in rev["source"]
    assert [o["obligation_definition_id"] for o in rev["obligation_definitions"]] == [odef_id]
    odef = client.get(f"/api/catalog/obligation-definitions/{odef_id}/trace", headers=H("actor-cco")).json()
    assert odef["missing_provision_ids"] == [] and odef["current_rule"] is None
    assert [r["source"]["fingerprint"] for r in odef["requirements"]] == [s["fingerprint"]]
    for path in (f"/api/catalog/sources/no-such/trace", "/api/catalog/requirements/no-such/trace",
                 "/api/catalog/obligation-definitions/no-such/trace"):
        assert client.get(path, headers=H("actor-cco")).status_code == 404, path


def test_traceability_validation_reports_missing_and_broken_links(client, world):
    from app.db import AdminSession
    from app.models import ContentRelease, Instrument, ObligationDefinition, RegulatorySource, RequirementVersion, RuleVersion
    from app.platform import canonical_json, next_record_seq, sha256_hex
    s = _source(client, "Entities shall retain records.").json()
    v = _requirement(client, s["source_id"], f"RT-{TAG}-BROKEN").json()
    with AdminSession() as db:
        inst_b = db.execute(select(Instrument).where(Instrument.identifier == INSTR_B)).scalars().one()
        o = ObligationDefinition(key=f"RT_{TAG}_BROKEN", title="Broken links (probe)", instrument_id=inst_b.instrument_id,
                                 provision_ids=["rt-no-such-provision", v["provisions"][0]["provision_id"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED")
        db.add(o)
        db.flush()
        # The same requirement identity recorded under a second instrument, below the API's identity check.
        db.add(RequirementVersion(requirement_key=f"RT-{TAG}-BROKEN", instrument_id=inst_b.instrument_id,
                                  source_id=s["source_id"], title="Duplicate identity", locator="Art. 1",
                                  valid_from=datetime(2025, 1, 1).date(), lifecycle="REVIEW_REQUIRED", record_seq=1))
        # The stored text changed after its fingerprint was taken.
        db.get(RegulatorySource, s["source_id"]).raw_text = "Entities shall retain records for ten years."
        # Probe release and rule with an unresolved source mapping and unbound release (well-formed cryptographic integrity)
        import json
        from app.engines import keys, rules
        from app.models import new_id
        probe_ast = {"node_type": "PREDICATE", "node_id": "p_probe", "operator": "EQ",
                     "args": [{"node_type": "FACT_REF", "node_id": "f", "fact": "entity.established_in_eu"},
                              {"node_type": "CONST", "node_id": "c", "type": "BOOLEAN", "value": True}],
                     "source_mappings": ["srcmap_probe_unresolved"]}
        canon_probe = rules.canonicalize(probe_ast)
        h_probe = rules.ast_hash(canon_probe, {})
        rv_probe_id, cr_probe_id = new_id(), new_id()
        manifest_probe = json.loads(canonical_json({"label": "probe-unbound", "rule_versions": [
            {"rule_version_id": rv_probe_id, "ast_sha256": h_probe, "valid_from": "2025-01-01"}]}))
        mb_probe = canonical_json(manifest_probe)
        cr_probe = ContentRelease(content_release_id=cr_probe_id, label="probe-unbound",
                                  manifest=manifest_probe, manifest_hash=sha256_hex(mb_probe),
                                  signature=keys.sign(mb_probe), signer_key_id=keys.signer_key_id(),
                                  released_by="actor-release", cell_received_seq=next_record_seq(db))
        db.add(cr_probe)
        db.flush()
        db.add(RuleVersion(rule_version_id=rv_probe_id, obligation_definition_id=o.obligation_definition_id, version=1,
                           valid_from=datetime(2025, 1, 1).date(), coverage_mode="ENCODED",
                           rule_schema_version=rules.SCHEMA_VERSION, operator_set_version=rules.OPERATOR_SET_VERSION,
                           canonical_ast=canon_probe, ast_hash=h_probe, required_facts=["entity.established_in_eu"], set_refs=[],
                           source_mappings=[], golden_vectors=[], status="PUBLISHED", content_release_id=cr_probe.content_release_id,
                           record_seq=next_record_seq(db)))
        db.commit()
        odef_id = o.obligation_definition_id
    report = client.get("/api/catalog/traceability/validation", headers=H(LEGAL)).json()
    assert report["ok"] is False and report["errors"] >= 4
    found = {(i["code"], i.get("obligation_definition_id") or i.get("source_id") or i.get("requirement_key"))
             for i in report["issues"]}
    assert ("OBLIGATION_PROVISION_MISSING", odef_id) in found
    assert ("OBLIGATION_INSTRUMENT_MISMATCH", odef_id) in found
    assert ("REQUIREMENT_KEY_MULTIPLE_INSTRUMENTS", f"RT-{TAG}-BROKEN") in found
    assert ("SOURCE_FINGERPRINT_MISMATCH", s["source_id"]) in found
    # Probe rule: AST nodes cite mappings their rule versions do not carry, and release is unbound.
    unresolved = {i["source_mapping_id"] for i in report["issues"] if i["code"] == "RULE_SOURCE_MAPPING_UNRESOLVED"}
    assert "srcmap_probe_unresolved" in unresolved
    assert report["by_code"]["RELEASE_REQUIREMENTS_UNBOUND"] >= 1
    # Seeded releases are remediated: zero unresolved source mappings
    assert not {"srcmap_aia_2_scope", "srcmap_aia_3_4"} & unresolved
    assert all(i["severity"] in ("ERROR", "WARNING") for i in report["issues"])
    # Content that rests on an altered source is never verified, and the requirement's own trace shows why.
    r = client.post(f"/api/catalog/requirements/{v['requirement_version_id']}/verify", headers=H(LEGAL))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SOURCE_NOT_GROUNDED"
    trace = client.get(f"/api/catalog/requirements/{v['requirement_version_id']}/trace", headers=H(AUTHOR)).json()
    assert trace["source"]["fingerprint_intact"] is False
    # A content-operations tool: organisations and the Super Admin cannot run it.
    for actor in ("actor-cco", "actor-ext-auditor", SUPER):
        assert client.get("/api/catalog/traceability/validation", headers=H(actor)).status_code == 403, actor
    assert client.get("/api/catalog/traceability/validation").status_code == 401


# ------------------------------------------------------------------------------------------------ Phase D/E: release
def _ast(tag):
    def pred(nid, fact, typ, value, sm):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [sm],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True, f"sm_{tag}_1"),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION", f"sm_{tag}_2")]}


VECTORS = [
    {"vector_id": "RT-POS", "vector_class": "POSITIVE", "description": "EU credit institution", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "APPLIES"},
    {"vector_id": "RT-NEG", "vector_class": "NEGATIVE", "description": "Not established in the EU", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "RT-UNK", "vector_class": "UNKNOWN", "description": "Entity type unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"},
]


def _mappings(tag, rv, source):
    p0, p1 = rv["provisions"][0], rv["provisions"][1]
    m = lambda sid, p: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                        "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                        "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    return [m(f"sm_{tag}_root", p0), m(f"sm_{tag}_1", p0), m(f"sm_{tag}_2", p1)]


def _reviewed_draft(client, odef_id, tag, rv, source, valid_from="2025-01-01"):
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": VECTORS,
        "source_mappings": _mappings(tag, rv, source), "valid_from": valid_from, "change_class": "EXECUTABLE_OUTCOME"})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, did, label):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": label, "changelog": label})


def test_release_gate_refuses_content_that_was_never_verified(client, chain):
    from app.db import AdminSession
    from app.models import ContentRelease, RuleVersion
    did = _reviewed_draft(client, chain["odef_id"], "g1", chain["v1"], chain["source"])
    chain["draft_id"] = did
    before = db_fingerprint()
    r = _publish(client, did, f"rt-{TAG}-gate")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RELEASE_CONTENT_NOT_VERIFIED"
    codes = {p["code"] for p in r.json()["detail"]["problems"]}
    assert codes == {"REQUIREMENT_NOT_VERIFIED", "PROVISION_NOT_VERIFIED"}  # INV-09 / FR-REG-002
    assert db_fingerprint() == before  # refused before anything was written
    with AdminSession() as db:
        assert db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == chain["odef_id"])).first() is None
        assert db.execute(select(ContentRelease).where(ContentRelease.label == f"rt-{TAG}-gate")).first() is None
    assert client.get(f"/api/catalog/requirements/{chain['v1']['requirement_version_id']}", headers=H(AUTHOR)).json()["lifecycle"] == "REVIEW_REQUIRED"


def test_verified_content_is_bound_into_the_signed_release_and_published(client, chain):
    from app.db import AdminSession
    from app.models import OutboxEvent
    v1, s = chain["v1"], chain["source"]
    assert client.post(f"/api/catalog/requirements/{v1['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    r = _publish(client, chain["draft_id"], f"rt-{TAG}-r1")
    assert r.status_code == 200, r.text
    cr, rule = r.json()["content_release"], r.json()["rule_version"]
    assert r.json()["requirement_versions"] == {"published": [v1["requirement_version_id"]], "superseded": []}
    [bound] = cr["manifest"]["requirement_versions"]
    digest = bound.pop("content_sha256")
    assert bound == {"requirement_version_id": v1["requirement_version_id"], "requirement_key": f"RT-{TAG}-CHAIN",
                     "source_id": s["source_id"], "source_fingerprint": s["fingerprint"], "source_locator": s["locator"],
                     "locator": "Art. 4", "provision_ids": sorted(p["provision_id"] for p in v1["provisions"]),
                     "content_profile": "za-requirement-content-v1"}
    assert len(digest) == 64 and int(digest, 16) >= 0  # SHA-256 hex over the released requirement + provision content
    check = client.get(f"/api/catalog/releases/{cr['content_release_id']}/verify", headers=H(AUTHOR)).json()
    assert check["manifest_hash_ok"] and check["integrity"] == "INTACT"
    assert {c["check"]: c["status"] for c in check["checks"]}["REQUIREMENT_CONTENT_DIGEST"] == "PASS"
    now = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}", headers=H(AUTHOR)).json()
    assert (now["lifecycle"], now["content_release_id"]) == ("PUBLISHED", cr["content_release_id"])
    assert {p["lifecycle"] for p in now["provisions"]} == {"PUBLISHED"}
    with AdminSession() as db:
        ev = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "requirement.version_published",
                                                  OutboxEvent.aggregate_id == v1["requirement_version_id"])).scalars().one()
        assert ev.tenant_id is None and ev.payload["content_release_id"] == cr["content_release_id"]
    t = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}/trace", headers=H("actor-cco")).json()
    cur = t["obligation_definitions"][0]["current_rule_version"]
    assert (cur["rule_version_id"], cur["binding_basis"], cur["release_binds"]) == (
        rule["rule_version_id"], "RELEASE_BOUND", [v1["requirement_version_id"]])
    report = client.get("/api/catalog/traceability/validation", headers=H(AUTHOR)).json()
    assert not [i for i in report["issues"] if i.get("rule_version_id") == rule["rule_version_id"]]  # fully grounded
    chain["rule"] = rule


def test_applicability_and_obligation_trace_to_the_exact_requirement_and_source(client, chain):
    """Facts -> evaluation -> adoption -> obligation, each traceable to the requirement version and source fingerprint
    the signed release bound (DOM §16); obligations only from adopted APPLIES determinations (DOM-INV-04)."""
    odef_id, v1, s = chain["odef_id"], chain["v1"], chain["source"]
    ev = client.post("/api/applicability/evaluations", headers=H(A_CCO), json={"subject_id": SUBJECT, "obligation_definition_id": odef_id})
    assert ev.status_code == 200, ev.text
    assert ev.json()["outcome"] == "UNCERTAIN"  # no facts yet: never coerced to DOES_NOT_APPLY
    # UNCERTAIN yields no determination to adopt (so no obligation), only missing-fact requests (DOM-INV-03/04).
    assert ev.json()["current_determination"] is None and ev.json()["missing_facts"]
    requested = {f["fact_key"] for f in client.get("/api/fact-requests", headers=H(A_CCO)).json()
                 if f.get("subject_id") == SUBJECT}
    assert requested & {"entity.established_in_eu", "entity.financial_entity_type"}
    assert not [o for o in client.get("/api/obligations", headers=H(A_CCO)).json() if o["obligation_definition_id"] == odef_id]
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        f = client.post(f"/api/subjects/{SUBJECT}/facts", headers=H(A_CCO), json={"fact_key": key, "value": value, "valid_from": at})
        assert f.status_code == 200, f.text
    ev = client.post("/api/applicability/evaluations", headers=H(A_CCO), json={"subject_id": SUBJECT, "obligation_definition_id": odef_id}).json()
    assert ev["outcome"] == "APPLIES" and ev["rule_version_id"] == chain["rule"]["rule_version_id"]
    prov = client.get(f"/api/applicability/evaluations/{ev['evaluation_id']}", headers=H(A_CCO)).json()["regulatory_provenance"]
    assert prov["binding_basis"] == "RELEASE_BOUND" and prov["rule_version_id"] == ev["rule_version_id"]
    [req] = prov["requirements"]
    assert req["requirement"]["requirement_version_id"] == v1["requirement_version_id"]
    assert req["source"]["fingerprint"] == s["fingerprint"] and req["fingerprint_matches_binding"] is True
    facts = {f["fact_key"]: f for f in prov["source_mapping_coverage"]["fact_mappings"]}
    assert set(facts) == {"entity.established_in_eu", "entity.financial_entity_type"}
    assert {m["requirement_version_id"] for f in facts.values() for m in f["resolved"]} == {v1["requirement_version_id"]}
    assert prov["source_mapping_coverage"]["unresolved"] == [] and {x["key"] for x in prov["required_facts"]} == set(facts)
    adopt = client.post(f"/api/applicability/determinations/{ev['current_determination']['lineage_id']}/attest",
                        headers=H(A_CCO), json={"decision": "ADOPT", "authority_basis": "Board delegation"})
    assert adopt.status_code == 200, adopt.text
    obls = [o for o in client.get("/api/obligations", headers=H(A_CCO)).json() if o["obligation_definition_id"] == odef_id]
    assert len(obls) == 1 and obls[0]["lifecycle"] == "ADOPTED"
    dossier = client.get(f"/api/obligations/{obls[0]['lineage_id']}", headers=H(A_CCO)).json()
    op = dossier["regulatory_provenance"]
    assert op["evaluation"]["evaluation_id"] == ev["evaluation_id"] and op["binding_basis"] == "RELEASE_BOUND"
    assert op["requirements"][0]["requirement"]["requirement_key"] == f"RT-{TAG}-CHAIN"
    again = client.post(f"/api/applicability/determinations/{ev['current_determination']['lineage_id']}/attest",
                        headers=H(A_CCO), json={"decision": "ADOPT", "authority_basis": "Board delegation"})
    assert again.status_code == 409  # no second adoption, so no duplicate obligation
    assert len([o for o in client.get("/api/obligations", headers=H(A_CCO)).json() if o["obligation_definition_id"] == odef_id]) == 1
    # Tenant isolation: another organisation reaches neither the evaluation nor the obligation.
    assert client.get(f"/api/applicability/evaluations/{ev['evaluation_id']}", headers=H(B_CCO)).status_code == 404
    assert client.get(f"/api/obligations/{obls[0]['lineage_id']}", headers=H(B_CCO)).status_code == 404
    chain["lineage"] = obls[0]["lineage_id"]


def test_a_new_released_version_supersedes_the_old_one_without_rewriting_it(client, chain):
    from app.db import AdminSession
    from app.models import ObligationDefinition, OutboxEvent
    v1, s = chain["v1"], chain["source"]
    v2 = _requirement(client, s["source_id"], f"RT-{TAG}-CHAIN", valid_from="2026-01-01").json()
    assert v2["version_number"] == 2
    assert client.post(f"/api/catalog/requirements/{v2['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:  # content authoring repoints the obligation definition at the new provisions
        db.get(ObligationDefinition, chain["odef_id"]).provision_ids = [p["provision_id"] for p in v2["provisions"]]
        db.commit()
    v1_before = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}", headers=H(AUTHOR)).json()
    did = _reviewed_draft(client, chain["odef_id"], "g2", v2, s, valid_from="2026-01-01")
    r = _publish(client, did, f"rt-{TAG}-r2")
    assert r.status_code == 200, r.text
    assert r.json()["requirement_versions"] == {"published": [v2["requirement_version_id"]],
                                                "superseded": [v1["requirement_version_id"]]}
    old = client.get(f"/api/catalog/requirements/{v1['requirement_version_id']}", headers=H(AUTHOR)).json()
    assert old["lifecycle"] == "SUPERSEDED" and {p["lifecycle"] for p in old["provisions"]} == {"SUPERSEDED"}

    def content(rv):  # everything but the lifecycle codes, at both levels
        return {**{k: v for k, v in rv.items() if k not in ("lifecycle", "provisions", "source")},
                "provisions": [{k: v for k, v in p.items() if k != "lifecycle"} for p in rv["provisions"]]}
    assert content(old) == content(v1_before)  # superseded, never rewritten
    with AdminSession() as db:
        assert db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "requirement.version_superseded",
                                                    OutboxEvent.aggregate_id == v1["requirement_version_id"])).first()
    hist = client.get(f"/api/catalog/requirements/{v2['requirement_version_id']}/history", headers=H(RELEASE)).json()
    assert [(v["version_number"], v["lifecycle"]) for v in hist["versions"]] == [(1, "SUPERSEDED"), (2, "PUBLISHED")]
    moves = [(e["object_id"], e["prior_state"], e["new_state"]) for e in hist["events"] if e["action"] == "requirement.lifecycle"]
    assert (v1["requirement_version_id"], "VERIFIED", "PUBLISHED") in moves
    assert (v1["requirement_version_id"], "PUBLISHED", "SUPERSEDED") in moves
    assert (v2["requirement_version_id"], "VERIFIED", "PUBLISHED") in moves
    # A superseded version's provisions can no longer be released (GLS §04: replaced for future valid time).
    with AdminSession() as db:
        db.get(ObligationDefinition, chain["odef_id"]).provision_ids = [p["provision_id"] for p in v1["provisions"]]
        db.commit()
    try:
        did3 = _reviewed_draft(client, chain["odef_id"], "g3", v1, s)
        r3 = _publish(client, did3, f"rt-{TAG}-r3")
        assert r3.status_code == 409 and {p["code"] for p in r3.json()["detail"]["problems"]} == {
            "REQUIREMENT_NOT_VERIFIED", "PROVISION_NOT_VERIFIED"}
    finally:
        with AdminSession() as db:
            db.get(ObligationDefinition, chain["odef_id"]).provision_ids = [p["provision_id"] for p in v2["provisions"]]
            db.commit()
    # The adopted obligation still traces to the version its evaluation actually used.
    op = client.get(f"/api/obligations/{chain['lineage']}", headers=H(A_CCO)).json()["regulatory_provenance"]
    assert op["requirements"][0]["requirement"]["requirement_version_id"] == v1["requirement_version_id"]


# ------------------------------------------------------------------------------------------------ authorization
def test_requirement_content_is_changed_only_by_the_content_roles_that_own_each_step(client, world):
    s = _source(client, "Entities shall train staff.").json()
    v = _requirement(client, s["source_id"], f"RT-{TAG}-AUTHZ").json()
    pid, rv_id = v["provisions"][0]["provision_id"], v["requirement_version_id"]
    calls = [("POST", "/api/catalog/sources", {"authority": "A", "jurisdiction": "EU", "source_type": "X", "title": "T",
                                                "locator": "L", "raw_text": "x"}),
             ("POST", f"/api/catalog/sources/{s['source_id']}/classify", {"instrument_identifier": INSTR}),
             ("POST", f"/api/catalog/sources/{s['source_id']}/extract", {}),
             ("POST", "/api/catalog/requirements", {}),
             ("PUT", f"/api/catalog/provisions/{pid}", {}),
             ("POST", f"/api/catalog/requirements/{rv_id}/verify", {})]
    before = db_fingerprint()
    for actor in ("actor-cco", "actor-ext-auditor", "actor-agent", SUPER, A_CCO):
        n = denials(actor)
        for method, path, body in calls:
            assert client.request(method, path, headers=H(actor), json=body).status_code == 403, (actor, path)
        assert denials(actor) - n == len(calls), actor  # every refusal recorded
    # Separation of content duties: the legal reviewer does not author; the author does not verify.
    for method, path, body in calls[:5]:
        assert client.request(method, path, headers=H(LEGAL), json=body).status_code == 403, path
    assert client.post(f"/api/catalog/requirements/{rv_id}/verify", headers=H(AUTHOR)).status_code == 403
    assert db_fingerprint() == before
    for method, path, body in calls:
        assert client.request(method, path, json=body).status_code == 401, path


# ------------------------------------------------------------------------------------------------ transactions
def test_a_failure_mid_creation_rolls_the_whole_version_back(client, world, monkeypatch):
    import app.engines.catalog as cat
    from app.db import AdminSession
    from app.models import Provision, RequirementVersion
    s = _source(client, "Entities shall report annually.").json()
    real = cat.audit

    def failing(db, **kw):
        if kw.get("action") == "requirement.create":
            raise RuntimeError("simulated failure after the version and its provisions were written")
        return real(db, **kw)
    monkeypatch.setattr(cat, "audit", failing)
    with pytest.raises(RuntimeError):
        _requirement(client, s["source_id"], f"RT-{TAG}-ROLLBACK")
    monkeypatch.undo()
    with AdminSession() as db:
        assert db.execute(select(RequirementVersion).where(
            RequirementVersion.requirement_key == f"RT-{TAG}-ROLLBACK")).first() is None
        assert db.execute(select(Provision).where(Provision.locator == "Art. 4(1)",
                                                  Provision.normalized_meaning == PROVISIONS[0]["normalized_meaning"])
                          .join(RequirementVersion, RequirementVersion.requirement_version_id == Provision.requirement_version_id)
                          .where(RequirementVersion.source_id == s["source_id"])).first() is None
    src = client.get(f"/api/catalog/sources/{s['source_id']}", headers=H(AUTHOR)).json()
    assert src["lifecycle"] == "CLASSIFIED" and src["requirement_versions"] == []  # its lifecycle move rolled back too
    assert _requirement(client, s["source_id"], f"RT-{TAG}-ROLLBACK").status_code == 201  # and the retry works


@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Needs concurrent transactions and row locks. The reason must be exactly "PostgreSQL-only": the CI backend-unit
        # gate (.github/workflows/ci.yml, --allow-skip-reason PostgreSQL-only) matches it verbatim.
        pytest.skip("PostgreSQL-only")
    return True


def _race(fn, actor_id):
    """Run `fn(ctx)` in two transactions at once (separate sessions, released together); each result is "ok" or the
    HTTP status it was refused with."""
    barrier, results = threading.Barrier(2), []

    def worker():
        from app.db import SessionLocal, set_principal
        from app.models import Actor
        from app.security import Ctx
        try:
            with SessionLocal() as db:
                set_principal(db, None, actor_id)
                ctx = Ctx(db=db, actor=db.get(Actor, actor_id))
                barrier.wait()
                fn(ctx)
                db.commit()
                results.append("ok")
        except HTTPException as e:
            results.append(e.status_code)
        except Exception as e:  # noqa: BLE001 - reported by the assertion
            results.append(repr(e))
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return sorted(results, key=str)


def test_concurrent_reviews_of_one_version_verify_it_exactly_once(client, world, pg):
    import app.engines.catalog as cat
    s = _source(client, "Entities shall test backups.").json()
    rv_id = _requirement(client, s["source_id"], f"RT-{TAG}-RACE").json()["requirement_version_id"]

    assert _race(lambda ctx: cat.verify(ctx, rv_id), LEGAL) == [409, "ok"]
    assert len(_audit("requirement.verify", object_id=rv_id)) == 1


def test_concurrent_creation_of_the_same_version_records_it_once(client, world, pg):
    import app.engines.catalog as cat
    from app.models import RegulatorySource
    s = _source(client, "Entities shall rotate keys.").json()
    key = f"RT-{TAG}-RACE2"

    def create(ctx):
        inst = cat.lock_instrument(ctx.db, INSTR)
        cat.create_version(ctx, source=ctx.db.get(RegulatorySource, s["source_id"]), instrument=inst, requirement_key=key,
                           title="Key rotation", locator="Art. 5", summary=None,
                           valid_from=datetime(2025, 1, 1).date(), valid_to=None, provisions=PROVISIONS[:1],
                           origin="ZOIKO_ENCODED", provision_origin="ZOIKO_ENCODED", provision_lifecycle="REVIEW_REQUIRED")
    assert _race(create, AUTHOR) == [409, "ok"]
    items = client.get("/api/catalog/requirements", params={"requirement_key": key}, headers=H(AUTHOR)).json()["items"]
    assert len(items) == 1
