"""Catalogue SatisfactionExpression templates, requirement-to-control traceability and mapping status.

Requirements exercised: ZA-STA-001 §07 (node grammar and "Required metadata": CONTROL "control ref, requirement
rationale, required=true", ANY / K_OF_N rationale; expression versioning and the version in force), STA-REQ-009/010/021;
ZA-DOM-001 §6.1 (ObligationDefinition and its SatisfactionExpressionTemplate "over control roles"), §7.1 (ControlMapping:
leaf -> control implementation or control role), §13, DOM-INV-11 (global content never references one organisation's
records); ZA-API-001 API-REQ-014.

Every test builds its own catalogue content and organisation; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal
from test_regulatory_traceability import LEGAL, _requirement, _source

TAG = secrets.token_hex(3).upper()
INSTR = f"MT-{TAG}"
AUTHOR = "actor-content-analyst"
URL = "/api/catalog/obligation-definitions"
TA = f"tenant-mt-{TAG.lower()}"
ENT, SUBJ = f"entity-mt-{TAG.lower()}", f"subj-mt-{TAG.lower()}"
OWNER, REVIEWER, CCO = f"mt-owner-{TAG.lower()}", f"mt-review-{TAG.lower()}", f"mt-cco-{TAG.lower()}"
INC, HUM, MON = "C_ICT_INCIDENT_PROCESS", "C_HUMAN_OVERSIGHT", "C_AI_MONITORING_LOGS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def content(client):
    from app.db import CELL_ID, AdminSession
    from app.models import Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title=f"Mapping probe {INSTR}", jurisdiction="EU",
                          authority="Probe Authority", coverage_mode="ENCODED"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        db.add(LegalEntity(entity_id=ENT, tenant_id=TA, legal_name=f"{TA} Bank AG", country="DE", home_cell_id=CELL_ID,
                           subject_id=SUBJ))
        db.add(Subject(subject_id=SUBJ, tenant_id=TA, entity_id=ENT, subject_type="LEGAL_ENTITY", display_name="MT Bank"))
        for aid, roles in ((OWNER, ["CONTROL_OWNER"]), (REVIEWER, ["RISK_APPROVER"]), (CCO, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=TA, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    s = _source(client, "Firms shall maintain an incident process and human oversight.", instrument=INSTR,
                locator=f"OJ L {TAG}/mt").json()
    rv = _requirement(client, s["source_id"], f"MT-{TAG}-V", instrument=INSTR).json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return {"source": s, "rv": rv}


def C(key, nid, rationale="Required by the provision.", **extra):
    n = {"node_type": "CONTROL", "node_id": nid, "control_key": key, **extra}
    if rationale is not None:
        n["rationale"] = rationale
    return n


def _create(client, content, key, template):
    rv = content["rv"]
    return client.post(URL, headers=H(AUTHOR), json={
        "key": key, "title": "Mapping probe definition", "instrument_identifier": INSTR, "subject_type": "LEGAL_ENTITY",
        "links": [{"requirement_version_id": rv["requirement_version_id"], "provision_id": rv["provisions"][0]["provision_id"]}],
        "satisfaction_template": template})


# ------------------------------------------------------------------------------------------------ catalogue templates
@pytest.mark.parametrize("template, code", [
    ({"node_type": "ALL", "node_id": "r", "children": [C(INC, "a", control_implementation_id="impl-x")]},
     "CONTROL_BINDING_NOT_ALLOWED"),                                                  # DOM-INV-11
    ({"node_type": "ALL", "node_id": "r", "children": [C(INC, "a", rationale=None)]}, "RATIONALE_REQUIRED"),
    ({"node_type": "ANY", "node_id": "r", "children": [C(INC, "a"), C(HUM, "b")]}, "RATIONALE_REQUIRED"),
    ({"node_type": "ALL", "node_id": "r", "children": [C(INC, "a", required=False)]}, "CONTROL_NOT_REQUIRED"),
    ({"node_type": "ALL", "node_id": "r", "children": [C(INC, "a"), C(INC, "b")]}, "DUPLICATE_CONTROL_LEAF"),
    ({"node_type": "ALL", "node_id": "r", "children": [C(INC, "a"), C(HUM, "a")]}, "DUPLICATE_NODE_ID"),
    ({"node_type": "ALL", "children": [C(INC, "a")]}, "NODE_ID_REQUIRED"),
    ({"node_type": "ALL", "node_id": "r", "children": [{"node_type": "OPTIONAL_EVIDENCE", "node_id": "o"}]},
     "TEMPLATE_STRUCTURE_INVALID"),                                                   # nothing binding
    ({"node_type": "K_OF_N", "node_id": "r", "k": 3, "rationale": "peers", "children": [C(INC, "a"), C(HUM, "b")]},
     "TEMPLATE_STRUCTURE_INVALID"),
    ({"node_type": "ALL", "node_id": "r", "children": [{"node_type": "XOR", "node_id": "x"}]}, "UNSUPPORTED_NODE"),
    ({"node_type": "COMPENSATION", "node_id": "c", "primary": C(INC, "p"), "compensating": C(HUM, "q")},
     "COMPENSATION_NOT_ALLOWED"),
    ({"node_type": "ALL", "node_id": "r", "children": [C("NO_SUCH_CONTROL", "a")]}, "UNKNOWN_CONTROL_DEFINITION"),
])
def test_invalid_templates_are_refused_with_every_problem_named(client, content, template, code):
    key = f"MT_{TAG}_BAD_{secrets.token_hex(3).upper()}"
    r = _create(client, content, key, template)
    assert r.status_code == 422, r.text
    assert code in [p["code"] for p in r.json()["detail"]["problems"]], r.json()
    assert key not in [o["key"] for o in client.get(URL, headers=H(AUTHOR)).json()]


def test_a_valid_template_is_traceable_from_requirement_to_control_roles(client, content):
    template = {"node_type": "ALL", "node_id": "root", "children": [
        C(INC, "inc"), {"node_type": "ANY", "node_id": "alt", "rationale": "Either control provides oversight.",
                        "children": [C(HUM, "hum"), C(MON, "mon")]},
        {"node_type": "OCCURRENCES", "node_id": "occ"}, {"node_type": "OPTIONAL_EVIDENCE", "node_id": "info"}]}
    r = _create(client, content, f"MT_{TAG}_OK", template)
    assert r.status_code == 201, r.text
    d = r.json()
    roles = [(x["node_id"], x["control_key"], x["binding"], x["known"]) for x in d["control_roles"]]
    assert roles == [("inc", INC, "SELECTOR", True), ("hum", HUM, "SELECTOR", True), ("mon", MON, "SELECTOR", True)]
    trace = client.get(f"{URL}/{d['obligation_definition_id']}/trace", headers=H(AUTHOR)).json()
    assert [x["control_key"] for x in trace["control_roles"]] == [INC, HUM, MON]
    assert trace["requirements"][0]["requirement"]["requirement_version_id"] == content["rv"]["requirement_version_id"]
    req = client.get(f"/api/catalog/requirements/{content['rv']['requirement_version_id']}/trace", headers=H(AUTHOR)).json()
    [down] = [o for o in req["obligation_definitions"] if o["obligation_definition_id"] == d["obligation_definition_id"]]
    assert down["control_keys"] == sorted([INC, HUM, MON])


def test_adoption_refuses_a_stored_template_with_an_exact_binding(client, content):
    """Legacy data the API now refuses: adopting it is a configuration error, never an obligation bound to a record of
    another organisation."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.engines.assurance import AssuranceConfigurationError
    from app.models import ObligationInstance, Subject
    odef = SimpleNamespace(obligation_definition_id=f"odef-legacy-{TAG}", key=f"MT_{TAG}_LEGACY", satisfaction_template={
        "node_type": "ALL", "node_id": "r", "children": [C(INC, "a", control_implementation_id="someone-elses-impl")]})
    with AdminSession() as db:
        subject = db.get(Subject, SUBJ)
        with pytest.raises(AssuranceConfigurationError) as e:
            appl._activate_obligation(db, SimpleNamespace(tenant_id=TA, revision_id="d", valid_from=T0),
                                      SimpleNamespace(attestation_id="a"), subject, odef, 1, CCO)
        db.rollback()
        assert e.value.detail["code"] == "CONTROL_MAPPING_INVALID"
        assert not db.execute(select(ObligationInstance).where(
            ObligationInstance.obligation_definition_id == odef.obligation_definition_id)).first()


# ------------------------------------------------------------------------------------------------ organisation versions
def _obligation(versions):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=SUBJ,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="p",
                                  attestation_id="p", lifecycle="ADOPTED", valid_from=T0, record_from_seq=next_record_seq(db)))
        for version, ast, vf, vt in versions:
            db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=version, ast=ast,
                                                 ast_hash=digest(ast), rationale="probe", valid_from=vf, valid_to=vt,
                                                 record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def ALL(*kids):
    return {"node_type": "ALL", "node_id": "root", "children": list(kids)}


def test_the_version_in_force_now_is_marked(client, content):
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    lineage = _obligation([(1, ALL(C(INC, "a")), T0, None),                    # superseded by v2
                           (2, ALL(C(HUM, "b")), datetime(2026, 3, 1, tzinfo=timezone.utc), None),  # in force now
                           (3, ALL(C(MON, "c")), future, None)])               # future-effective
    vs = client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(CCO)).json()
    assert [(v["version"], v["in_force_now"]) for v in vs] == [(1, False), (2, True), (3, False)]
    ended = _obligation([(1, ALL(C(INC, "a")), T0, datetime(2026, 2, 1, tzinfo=timezone.utc))])
    assert [v["in_force_now"] for v in client.get(f"/api/obligations/{ended}/expression-versions",
                                                  headers=H(CCO)).json()] == [False]  # none in force: never guessed


def test_repeated_evaluation_of_the_same_inputs_is_identical(client, content):
    lineage = _obligation([(1, ALL(C(INC, "a")), T0, None)])
    body = {"period_from": "2026-01-01T00:00:00Z", "period_to": "2026-02-01T00:00:00Z"}
    first = client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO), json=body).json()
    cutoff = first["record_cutoff_seq"]
    runs = [client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO),
                        json={**body, "record_cutoff_seq": cutoff}).json() for _ in range(3)]
    assert {(r["state"], r["dependency_digest"]) for r in runs} == {(first["state"], first["dependency_digest"])}


def test_a_failure_while_recording_a_version_leaves_nothing_behind(client, content, monkeypatch):
    from app.engines import satisfaction
    lineage = _obligation([(1, ALL(C(INC, "a")), T0, None)])

    def broken(*a, **k):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(satisfaction, "audit", broken)
    with pytest.raises(RuntimeError):
        client.post(f"/api/obligations/{lineage}/expression-versions", headers=H(REVIEWER), json={
            "ast": ALL(C(HUM, "b")), "valid_from": "2026-01-01T00:00:00Z", "rationale": "probe"})
    monkeypatch.undo()
    assert [v["version"] for v in client.get(f"/api/obligations/{lineage}/expression-versions",
                                             headers=H(CCO)).json()] == [1]


@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def test_a_mapping_change_and_a_compensation_approval_never_collide(client, content, pg):
    """Both write a new expression version for the same obligation; the shared lock gives them distinct numbers."""
    from app.db import SessionLocal, set_principal
    from app.engines import governance, satisfaction
    from app.models import Actor
    from app.security import Ctx
    lineage = _obligation([(1, ALL(C(INC, "a", rationale="r")), T0, None)])
    c = client.post("/api/compensations", headers=H(OWNER), json={
        "obligation_lineage_id": lineage, "primary_control_key": INC, "compensating_control_keys": [HUM],
        "equivalence_rationale": "Human oversight addresses the same incident objective.", "valid_from": "2026-01-01T00:00:00Z"})
    assert c.status_code == 200, c.text
    cid = c.json()["compensation_id"]
    barrier, results = threading.Barrier(2), []

    def run(fn):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, REVIEWER)
                ctx = Ctx(db=db, actor=db.get(Actor, REVIEWER))
                barrier.wait()
                fn(ctx)
                db.commit()
                results.append("ok")
        except HTTPException as e:
            results.append(e.status_code)
        except Exception as e:  # noqa: BLE001
            results.append(repr(e))
    change = lambda ctx: satisfaction.create_version(ctx, lineage_id=lineage, ast=ALL(C(INC, "a"), C(MON, "m")),
                                                     valid_from=T0, valid_to=None, rationale="add monitoring")
    approve = lambda ctx: governance.decide_compensation(ctx.db, tenant_id=TA, actor_id=REVIEWER, actor=ctx.actor,
                                                         comp_id=cid, decision="APPROVE", reason="ok")
    threads = [threading.Thread(target=run, args=(f,)) for f in (change, approve)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    vs = client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(CCO)).json()
    numbers = [v["version"] for v in vs]
    assert numbers == sorted(set(numbers)) and numbers[0] == 1  # never two versions with one number
    assert len(numbers) == 1 + results.count("ok"), (results, numbers)
