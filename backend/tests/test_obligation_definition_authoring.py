"""Obligation definition authoring (docs/obligation-definition-authoring-implementation-report.md).

Requirements exercised: ZA-DOM-001 §6.1 (ObligationDefinition "derived from one or more provision versions", owned by
Regulatory Content), §13 (ProvisionVersion -> ObligationDefinition 1:N, "each link is explicit"; ObligationDefinition ->
RuleVersion 1:N), DOM-INV-03/04/13; ZA-GLS-001 §04 (content lifecycle) and coverage-mode codes; ZA-DAT-001 §05
(obligation_definition: provision refs, subject type, coverage mode), DAT-REQ-012 (published catalogue immutable);
ARCH INV-06/INV-09 (release gate); ZA-STA-001 STA-REQ-009/010 (SatisfactionExpression nodes); ZA-API-001 API-REQ-003
(named actions), API-REQ-014/033.

Every test builds its own instruments, requirement versions and definitions; the seeded catalogue is only read.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint, denials
from test_regulatory_traceability import _publish, _requirement, _reviewed_draft, _source
from test_rule_integrity import _tampered

TAG = secrets.token_hex(3).upper()
INSTR, OTHER = f"ODA-{TAG}", f"ODA-{TAG}-X"
AUTHOR, LEGAL, RELEASE = "actor-content-analyst", "actor-content-legal", "actor-release"
TA, TB, A_CCO, B_CCO, SUPER, SUBJECT = "tenant-oda-a", "tenant-oda-b", "oda-a-cco", "oda-b-cco", "oda-super", "subj-oda-a"
URL = "/api/catalog/obligation-definitions"


@pytest.fixture(scope="module")
def content(client):
    """Instruments; a verified, a reviewed (REVIEW_REQUIRED), an AI-extracted and an other-instrument requirement
    version; two probe organisations and a Super Admin."""
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for ident in (INSTR, OTHER):
            db.add(Instrument(identifier=ident, title=f"Authoring probe {ident}", jurisdiction="EU",
                              authority="Probe Authority", coverage_mode="ENCODED"))
        for tid, sid in ((TA, SUBJECT), (TB, "subj-oda-b")):
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
    s = _source(client, "Credit institutions shall keep a register and shall review it annually.", instrument=INSTR,
                locator=f"OJ L {TAG}/oda").json()
    verified = _requirement(client, s["source_id"], f"ODA-{TAG}-V", instrument=INSTR).json()
    assert client.post(f"/api/catalog/requirements/{verified['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    reviewed = _requirement(client, s["source_id"], f"ODA-{TAG}-R", instrument=INSTR).json()
    sx = _source(client, "Firms shall report incidents.", instrument=OTHER).json()
    other = _requirement(client, sx["source_id"], f"ODA-{TAG}-O", instrument=OTHER).json()
    ext = client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR), json={
        "instrument_identifier": INSTR, "requirement_key": f"ODA-{TAG}-AI", "requirement_title": "AI proposal",
        "locator": "Art. 8", "valid_from": "2025-01-01"}).json()
    return {"source": s, "verified": verified, "reviewed": reviewed, "other": other,
            "ai": {"requirement_version_id": ext["requirement_version"]["requirement_version_id"],
                   "provisions": ext["proposed_provisions"]}}


def _links(rv, idx=(0, 1)):
    return [(rv["requirement_version_id"], rv["provisions"][i]["provision_id"]) for i in idx]


def _body(key, links, **extra):
    return {"key": key, "title": "Maintain the ICT register (probe)", "instrument_identifier": INSTR,
            "subject_type": "LEGAL_ENTITY",
            "links": [{"requirement_version_id": rv, "provision_id": p} for rv, p in links], **extra}


def _create(client, key_, links_, actor=AUTHOR, **overrides):
    """POST a definition; `overrides` replace or add body fields (including `key`)."""
    return client.post(URL, headers=H(actor), json={**_body(key_, links_), **overrides})


def _audits(object_id, action):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        return db.execute(select(AuditEvent).where(AuditEvent.action == action, AuditEvent.object_id == object_id)
                          .order_by(AuditEvent.record_seq, AuditEvent.id)).scalars().all()


def _control_key(client) -> str:
    return client.get("/api/catalog/controls", headers=H(AUTHOR)).json()[0]["key"]


# ------------------------------------------------------------------------------------------------ creation
def test_a_definition_is_created_with_explicit_validated_links_and_is_traceable(client, content):
    v, s = content["verified"], content["source"]
    # ZA-STA-001 §07 required metadata: a CONTROL leaf carries its requirement rationale.
    template = {"node_type": "ALL", "node_id": "t_root", "children": [
        {"node_type": "CONTROL", "node_id": "t_1", "control_key": _control_key(client),
         "rationale": "The register duty is met by this control."}]}
    r = _create(client, f"ODA_{TAG}_CREATE", _links(v), satisfaction_template=template, reason="New register duty")
    assert r.status_code == 201, r.text
    d = r.json()
    assert (d["released"], d["current_rule_version"], d["instrument"]["identifier"]) == (False, None, INSTR)
    assert d["provision_ids"] == [p for _, p in _links(v)] and d["satisfaction_template"] == template
    assert [(r["node_id"], r["control_key"], r["binding"], r["known"]) for r in d["control_roles"]] == \
        [("t_1", _control_key(client), "SELECTOR", True)]
    assert [(x["requirement_version"]["requirement_version_id"], x["provision_id"], x["source"]["fingerprint"])
            for x in d["links"]] == [(rv, p, s["fingerprint"]) for rv, p in _links(v)]
    assert client.get(f"{URL}/{d['obligation_definition_id']}", headers=H("actor-cco")).json() == d  # read by orgs too
    [a] = _audits(d["obligation_definition_id"], "obligation_definition.create")
    assert a.actor_id == AUTHOR and a.tenant_id is None and "New register duty" in a.reason and v["requirement_version_id"] in a.reason
    # Forward (requirement -> definition) and reverse (definition -> requirement -> source) traceability.
    fwd = client.get(f"/api/catalog/requirements/{v['requirement_version_id']}/trace", headers=H(AUTHOR)).json()
    assert d["obligation_definition_id"] in {o["obligation_definition_id"] for o in fwd["obligation_definitions"]}
    rev = client.get(f"{URL}/{d['obligation_definition_id']}/trace", headers=H(AUTHOR)).json()
    assert rev["missing_provision_ids"] == [] and [r_["source"]["fingerprint"] for r_ in rev["requirements"]] == [s["fingerprint"]]
    assert d["key"] in {o["key"] for o in client.get(URL, headers=H(AUTHOR)).json()}


def test_definition_input_is_validated_against_the_catalogue(client, content):
    v, rev, other, ai = content["verified"], content["reviewed"], content["other"], content["ai"]
    ok = _links(v)
    cases_422 = [
        ({"provision_ids": ["x"]}, ok), ({"released": True}, ok), ({}, []), ({"key": "x" * 121}, ok), ({"title": " "}, ok),
        ({"subject_type": "TENANT"}, ok), ({"coverage_mode": "COMPLIANT"}, ok), ({"schedule_kind": "ONE_TIME"}, ok),
        ({"schedule_kind": "RECURRING"}, ok), ({"recurrence_days": 30}, ok),
        ({"schedule_kind": "RECURRING", "recurrence_days": 0}, ok),
        ({"satisfaction_template": {"node_type": "COMPENSATION", "primary": {}, "compensating": {}}}, ok),
        ({"satisfaction_template": {"node_type": "ALL", "children": []}}, ok),
        ({"satisfaction_template": {"node_type": "K_OF_N", "k": 3, "children": [
            {"node_type": "CONTROL", "control_key": _control_key(client)}] * 2}}, ok),
        ({"satisfaction_template": {"node_type": "CONTROL", "control_key": "NO_SUCH_CONTROL"}}, ok),
        ({"satisfaction_template": {"node_type": "SCRIPT", "code": "x"}}, ok),
        ({}, [ok[0], ok[0]]),  # the same provision twice
    ]
    before = db_fingerprint()
    for extra, links in cases_422:
        r = _create(client, f"ODA_{TAG}_BAD", links, **extra)
        assert r.status_code == 422, (extra, links, r.text)
    assert _create(client, f"ODA_{TAG}_BAD", ok, instrument_identifier="NO-SUCH").status_code == 404
    for links, code in (([(v["requirement_version_id"], "no-such-provision")], "PROVISION_NOT_FOUND"),
                        ([("no-such-rv", ok[0][1])], "REQUIREMENT_VERSION_NOT_FOUND")):
        r = _create(client, f"ODA_{TAG}_BAD", links)
        assert r.status_code == 404 and r.json()["detail"]["error"] == code, code
    for links, code in (([(v["requirement_version_id"], rev["provisions"][0]["provision_id"])], "PROVISION_NOT_IN_REQUIREMENT_VERSION"),
                        (_links(other, (0,)), "REQUIREMENT_INSTRUMENT_MISMATCH"),
                        (_links(ai, (0,)), "CONTENT_NOT_LINKABLE")):
        r = _create(client, f"ODA_{TAG}_BAD", links)
        assert r.status_code == 409 and r.json()["detail"]["error"] == code, (code, r.text)
    assert db_fingerprint() == before  # every refusal wrote nothing
    assert _create(client, f"ODA_{TAG}_DUP", ok).status_code == 201
    dup = _create(client, f"ODA_{TAG}_DUP", _links(rev))
    assert dup.status_code == 409 and dup.json()["detail"]["error"] == "OBLIGATION_DEFINITION_KEY_EXISTS"


@pytest.mark.parametrize("lifecycle", ["WITHDRAWN", "SUPERSEDED", "AI_EXTRACTED"])
def test_withdrawn_superseded_or_unreviewed_content_cannot_be_linked(client, content, lifecycle):
    p = content["reviewed"]["provisions"][1]
    with _tampered("catalog_provision", "provision_id", p["provision_id"], lifecycle=lifecycle):
        r = _create(client, f"ODA_{TAG}_{lifecycle}", [(content["reviewed"]["requirement_version_id"], p["provision_id"])])
    assert r.status_code == 409 and r.json()["detail"] == {**r.json()["detail"], "error": "CONTENT_NOT_LINKABLE",
                                                            "lifecycle": lifecycle}


# ------------------------------------------------------------------------------------------------ links
def test_links_are_added_and_removed_only_by_named_actions_with_history(client, content):
    rev, other = content["reviewed"], content["other"]
    (rv_id, p0), (_, p1) = _links(rev)
    d = _create(client, f"ODA_{TAG}_LINKS", [(rv_id, p0)]).json()
    oid = d["obligation_definition_id"]
    r = client.post(f"{URL}/{oid}/links", headers=H(AUTHOR), json={"requirement_version_id": rv_id, "provision_id": p1,
                                                                    "reason": "Second duty of the same article"})
    assert r.status_code == 201 and r.json()["provision_ids"] == [p0, p1]
    again = client.post(f"{URL}/{oid}/links", headers=H(AUTHOR), json={"requirement_version_id": rv_id, "provision_id": p1})
    assert again.status_code == 409 and again.json()["detail"]["error"] == "DUPLICATE_LINK"
    cross = client.post(f"{URL}/{oid}/links", headers=H(AUTHOR), json={
        "requirement_version_id": other["requirement_version_id"], "provision_id": other["provisions"][0]["provision_id"]})
    assert cross.status_code == 409 and cross.json()["detail"]["error"] == "REQUIREMENT_INSTRUMENT_MISMATCH"
    assert client.post(f"{URL}/{oid}/links/{p0}/remove", headers=H(AUTHOR), json={}).status_code == 422  # reason required
    rm = client.post(f"{URL}/{oid}/links/{p0}/remove", headers=H(AUTHOR), json={"reason": "Belongs to another duty"})
    assert rm.status_code == 200 and rm.json()["provision_ids"] == [p1]
    gone = client.post(f"{URL}/{oid}/links/{p0}/remove", headers=H(AUTHOR), json={"reason": "again"})
    assert gone.status_code == 404 and gone.json()["detail"]["error"] == "LINK_NOT_FOUND"
    last = client.post(f"{URL}/{oid}/links/{p1}/remove", headers=H(AUTHOR), json={"reason": "would leave none"})
    assert last.status_code == 409 and last.json()["detail"]["error"] == "LAST_LINK"
    history = [(a.action, a.actor_id) for a in _audits(oid, "obligation_definition.link") + _audits(oid, "obligation_definition.unlink")]
    assert history == [("obligation_definition.link", AUTHOR), ("obligation_definition.unlink", AUTHOR)]
    assert "Belongs to another duty" in _audits(oid, "obligation_definition.unlink")[0].reason
    for path in (f"{URL}/no-such/links", f"{URL}/no-such/links/{p1}/remove"):
        body = {"requirement_version_id": rv_id, "provision_id": p1, "reason": "x" * 3} if path.endswith("links") else {"reason": "xyz"}
        assert client.post(path, headers=H(AUTHOR), json=body).status_code == 404, path


# ------------------------------------------------------------------------------------------------ release
@pytest.fixture(scope="module")
def released(client, content):
    """A definition authored through the API, then released through every rule-publication gate."""
    v, s = content["verified"], content["source"]
    d = _create(client, f"ODA_{TAG}_REL", _links(v)).json()
    did = _reviewed_draft(client, d["obligation_definition_id"], "oda", v, s)
    r = _publish(client, did, f"oda-{TAG}")
    assert r.status_code == 200, r.text
    return {"definition": d, "release": r.json()["content_release"], "rule": r.json()["rule_version"]}


def test_a_released_definition_is_immutable_and_its_release_binds_the_authored_links(client, content, released):
    d, cr = released["definition"], released["release"]
    oid = d["obligation_definition_id"]
    now = client.get(f"{URL}/{oid}", headers=H(AUTHOR)).json()
    assert now["released"] is True and now["current_rule_version"]["content_release_id"] == cr["content_release_id"]
    [bound] = cr["manifest"]["requirement_versions"]
    assert bound["provision_ids"] == sorted(d["provision_ids"])  # the signed release binds exactly the authored links
    rev = content["reviewed"]
    before = db_fingerprint()
    add = client.post(f"{URL}/{oid}/links", headers=H(AUTHOR), json={
        "requirement_version_id": rev["requirement_version_id"], "provision_id": rev["provisions"][0]["provision_id"]})
    rm = client.post(f"{URL}/{oid}/links/{d['provision_ids'][0]}/remove", headers=H(AUTHOR), json={"reason": "change"})
    for r in (add, rm):
        assert r.status_code == 409 and r.json()["detail"]["error"] == "OBLIGATION_DEFINITION_RELEASED"
    seeded = next(o for o in client.get(URL, headers=H(AUTHOR)).json() if o["key"] == "EUAIA_DEPLOYER_HR_OVERSIGHT")
    r = client.post(f"{URL}/{seeded['obligation_definition_id']}/links/{seeded['provision_ids'][0]}/remove",
                    headers=H(AUTHOR), json={"reason": "probe"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "OBLIGATION_DEFINITION_RELEASED"
    assert db_fingerprint() == before
    # The historical release is untouched and still verifies as intact.
    check = client.get(f"/api/catalog/releases/{cr['content_release_id']}/verify", headers=H(AUTHOR)).json()
    assert check["integrity"] == "INTACT" and check["signature"]["status"] == "VALID"


def test_authored_definition_flows_through_applicability_and_adoption_unchanged(client, content, released):
    """Regression on the established algorithms: UNCERTAIN yields no obligation; APPLIES + attested adoption yields
    one obligation whose details name the exact requirement version and provisions; re-adoption is refused."""
    oid, v = released["definition"]["obligation_definition_id"], content["verified"]
    ev = client.post("/api/applicability/evaluations", headers=H(A_CCO), json={"subject_id": SUBJECT, "obligation_definition_id": oid})
    assert ev.status_code == 200 and ev.json()["outcome"] == "UNCERTAIN" and ev.json()["current_determination"] is None
    assert not [o for o in client.get("/api/obligations", headers=H(A_CCO)).json() if o["obligation_definition_id"] == oid]
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{SUBJECT}/facts", headers=H(A_CCO),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
    ev = client.post("/api/applicability/evaluations", headers=H(A_CCO), json={"subject_id": SUBJECT, "obligation_definition_id": oid}).json()
    assert ev["outcome"] == "APPLIES"
    lineage = ev["current_determination"]["lineage_id"]
    assert client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(A_CCO),
                       json={"decision": "ADOPT", "authority_basis": "Board delegation"}).status_code == 200
    [o] = [o for o in client.get("/api/obligations", headers=H(A_CCO)).json() if o["obligation_definition_id"] == oid]
    dossier = client.get(f"/api/obligations/{o['lineage_id']}", headers=H(A_CCO)).json()
    assert sorted((p["requirement_version_id"], p["provision_id"]) for p in dossier["provisions"]) == sorted(_links(v))
    prov = dossier["regulatory_provenance"]
    assert prov["binding_basis"] == "RELEASE_BOUND"
    assert [(r["requirement"]["requirement_version_id"], sorted(p["provision_id"] for p in r["provisions"]))
            for r in prov["requirements"]] == [(v["requirement_version_id"], sorted(p for _, p in _links(v)))]
    assert client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(A_CCO),
                       json={"decision": "ADOPT", "authority_basis": "Board delegation"}).status_code == 409
    assert len([x for x in client.get("/api/obligations", headers=H(A_CCO)).json() if x["obligation_definition_id"] == oid]) == 1
    assert client.get(f"/api/obligations/{o['lineage_id']}", headers=H(B_CCO)).status_code == 404  # tenant isolation


# ------------------------------------------------------------------------------------------------ access
def test_authoring_is_refused_to_everyone_but_content_authors_and_recorded(client, content):
    v = content["verified"]
    oid = _create(client, f"ODA_{TAG}_AUTHZ", _links(v, (0,))).json()["obligation_definition_id"]
    calls = [(URL, _body(f"ODA_{TAG}_X", _links(v))),
             (f"{URL}/{oid}/links", {"requirement_version_id": v["requirement_version_id"], "provision_id": v["provisions"][1]["provision_id"]}),
             (f"{URL}/{oid}/links/{v['provisions'][0]['provision_id']}/remove", {"reason": "probe"})]
    before = db_fingerprint()
    for actor in ("actor-cco", "actor-ext-auditor", "actor-agent", SUPER, LEGAL, RELEASE, A_CCO):
        n = denials(actor)
        for path, body in calls:
            assert client.post(path, headers=H(actor), json=body).status_code == 403, (actor, path)
        assert denials(actor) - n == len(calls), actor
    for path, body in calls:
        assert client.post(path, json=body).status_code == 401, path
    assert db_fingerprint() == before
    # Definitions are global catalogue content: organisations read them, and nothing tenant-specific is in them.
    view = client.get(f"{URL}/{oid}", headers=H(B_CCO))
    assert view.status_code == 200 and TA not in view.text and TB not in view.text
    assert client.get(f"{URL}/{oid}", headers=H(SUPER)).status_code == 403
    assert client.get(f"{URL}/no-such", headers=H(AUTHOR)).status_code == 404


# ------------------------------------------------------------------------------------------------ transactions
def test_a_failure_mid_creation_leaves_nothing_behind(client, content, monkeypatch):
    import app.engines.catalog as cat
    from app.db import AdminSession
    from app.models import ObligationDefinition
    real = cat.audit

    def failing(db, **kw):
        if kw.get("action") == "obligation_definition.create":
            raise RuntimeError("simulated failure after the definition was written")
        return real(db, **kw)
    monkeypatch.setattr(cat, "audit", failing)
    with pytest.raises(RuntimeError):
        _create(client, f"ODA_{TAG}_ROLLBACK", _links(content["verified"]))
    monkeypatch.undo()
    with AdminSession() as db:
        assert db.execute(select(ObligationDefinition).where(ObligationDefinition.key == f"ODA_{TAG}_ROLLBACK")).first() is None
    assert _create(client, f"ODA_{TAG}_ROLLBACK", _links(content["verified"])).status_code == 201


@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Needs concurrent transactions, the unique constraint and row locks. The reason must be exactly
        # "PostgreSQL-only": the CI backend-unit gate matches it verbatim (--allow-skip-reason PostgreSQL-only).
        pytest.skip("PostgreSQL-only")
    return True


def _race(fn):
    barrier, results = threading.Barrier(2), []

    def worker():
        from app.db import SessionLocal, set_principal
        from app.models import Actor
        from app.security import Ctx
        try:
            with SessionLocal() as db:
                set_principal(db, None, AUTHOR)
                ctx = Ctx(db=db, actor=db.get(Actor, AUTHOR))
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


def test_concurrent_creation_of_one_key_creates_one_definition(client, content, pg):
    import app.engines.catalog as cat
    key = f"ODA_{TAG}_RACE"

    def create(ctx):
        cat.create_definition(ctx, key=key, title="Race", instrument=cat.lock_instrument(ctx.db, INSTR),
                              subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                              recurrence_days=None, material=True, satisfaction_template=None,
                              links=_links(content["verified"]), reason="race")
    assert _race(create) == [409, "ok"]
    assert [o["key"] for o in client.get(URL, headers=H(AUTHOR)).json()].count(key) == 1


def test_concurrent_links_of_one_provision_record_it_once(client, content, pg):
    import app.engines.catalog as cat
    rev = content["reviewed"]
    (rv_id, p0), (_, p1) = _links(rev)
    oid = _create(client, f"ODA_{TAG}_RACE_LINK", [(rv_id, p0)]).json()["obligation_definition_id"]
    assert _race(lambda ctx: cat.add_link(ctx, oid, rv_id, p1, "race")) == [409, "ok"]
    assert client.get(f"{URL}/{oid}", headers=H(AUTHOR)).json()["provision_ids"] == [p0, p1]
    assert len(_audits(oid, "obligation_definition.link")) == 1
