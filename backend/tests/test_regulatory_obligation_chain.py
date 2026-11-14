"""Regulatory-to-obligation chain, end to end: Source -> RequirementVersion -> Provision -> (signed RuleVersion) -> facts ->
ApplicabilityEvaluation -> ApplicabilityDetermination -> AdoptionAttestation -> ObligationInstance -> SatisfactionExpression
-> controls, read through the API now and as of each record sequence (ZA-DOM-001 §1/§16 canonical chain; ZA-TIM-001
§12; ZA-APP-001 §06/§12/§16; ZA-RUL-001 §02/§12).

Guards, no source change: every link resolves to the exact record the decision used; the chain stays explainable through
a correction release, a superseded requirement version, re-adoption, closure and reopening; another organisation reaches
no link by ID (API and, on PostgreSQL, row-level security); a correction publication racing an adoption never leaves an
obligation whose provenance differs from the decision it rests on.

Known gaps, deliberately not asserted (they need schema decisions): catalogue lifecycle codes (a requirement version's
PUBLISHED / SUPERSEDED) are current values even in a historical read; a closed obligation's earlier period is excluded
from assessment. Runs on SQLite and PostgreSQL 17; concurrency and direct RLS reads on PostgreSQL only.
"""
from __future__ import annotations

import itertools
import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"ROC-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-roca-{TAG.lower()}", f"tenant-rocb-{TAG.lower()}"
CCO, CCO_B = f"roc-cco-{TAG.lower()}", f"rocb-cco-{TAG.lower()}"
CONTROL = f"CTRL-{TAG}-ROC"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
BASIS = "Board delegation ROC"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
_n = itertools.count()


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


def _requirement(client, key: str, valid_from: str, action: str) -> dict:
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
        "title": "Register requirement", "locator": "Art. 1", "valid_from": valid_from, "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": action,
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": f"{action}.",
             "evidence_expectation": "Register"}]})
    assert r.status_code == 201, r.text
    assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    return r.json()


def _definition(rv: dict, template=True) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"ROC_{TAG}_{secrets.token_hex(2)}", title="Chain obligation",
                                 instrument_id=inst.instrument_id, provision_ids=[p["provision_id"] for p in rv["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True, satisfaction_template={"node_type": "CONTROL", "node_id": "c",
                                                                       "control_key": CONTROL} if template else None)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _shadowed_draft(client, odef: str, rv: dict, change_class="EXECUTABLE_OUTCOME") -> str:
    t, p = secrets.token_hex(3), rv["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(t), "golden_vectors": _vectors(),
        "valid_from": "2025-01-01", "change_class": change_class,
        "source_mappings": [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, odef: str, rv: dict, change_class="EXECUTABLE_OUTCOME", reason=None) -> dict:
    did = _shadowed_draft(client, odef, rv, change_class)
    body = {"label": f"roc-{secrets.token_hex(4)}", "changelog": "roc", **({"correction_reason": reason} if reason else {})}
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import ControlDefinition, Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Chain Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=CONTROL, title="Chain control", objective="Probe.", category="RISK_MANAGEMENT"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="ROC CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="ROC CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Chain Act",
        "locator": f"OJ L {TAG}/roc", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    S["rv"] = _requirement(client, f"ROC-{TAG}-REQ", "2025-01-01", "keep register")
    S["odef"] = _definition(S["rv"])
    S["pub"] = _publish(client, S["odef"], S["rv"])
    return True


def _proposed(name, odef=None) -> dict:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    S.setdefault("facts", {})[sid] = [_fact(sid, EU, True), _fact(sid, KIND, "CREDIT_INSTITUTION")]
    ev = _evaluate(sid, odef or S["odef"])
    assert ev["current_determination"]["state"] == "PROPOSED"
    return {"sid": sid, "ev": ev, "lineage": ev["current_determination"]["lineage_id"]}


def _fact(sid, key, value, *, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=1 if change else 40)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "Changed"}
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text
    return r.json()["revisions"][-1]["revision_id"]


def _evaluate(sid, odef):
    r = S["client"].post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": odef})
    assert r.status_code == 200, r.text
    return r.json()


def _attest(lineage, actor=CCO, **extra):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/attest", headers=H(actor),
                            json={"decision": "ADOPT", "authority_basis": BASIS, **extra})


def _flag(x, kind) -> str:
    _fact(x["sid"], KIND, kind, change=True)
    [cur] = [d for d in _determinations(x["lineage"]) if d.record_to_seq is None]
    assert cur.needs_review, cur.state
    return cur.revision_id


def _determinations(lineage):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        return db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.lineage_id == lineage)
                          .order_by(ApplicabilityDetermination.record_from_seq)).scalars().all()


def _obligations(sid, odef=None):
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        q = select(ObligationInstance).where(ObligationInstance.subject_id == sid)
        if odef:
            q = q.where(ObligationInstance.obligation_definition_id == odef)
        return db.execute(q.order_by(ObligationInstance.record_from_seq)).scalars().all()


def _dossier(lineage, as_of=None, actor=CCO):
    return S["client"].get(f"/api/obligations/{lineage}", headers=H(actor),
                           params={"as_of_record_seq": as_of} if as_of is not None else None)


def _bound(d) -> list:
    return sorted((q["requirement"]["requirement_version_id"], q["bound_source_fingerprint"], q["fingerprint_matches_binding"],
                   q["content_matches_binding"], tuple(sorted(p["provision_id"] for p in q["provisions"])))
                  for q in d["regulatory_provenance"]["requirements"])


def _verified(cr_id) -> bool:
    v = S["client"].get(f"/api/catalog/releases/{cr_id}/verify", headers=H(AUTHOR)).json()
    return v["integrity"] == "INTACT" and v["signature"]["status"] == "VALID" and not [c for c in v["checks"] if c["status"] == "FAIL"]


def _replays(sid) -> list:
    evs = S["client"].get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": sid}).json()
    return [S["client"].get(f"/api/applicability/evaluations/{e['evaluation_id']}", headers=H(CCO)).json()["replay"]["status"]
            for e in evs]


# ------------------------------------------------------------------------------------------------ the chain
def test_an_adopted_obligation_traces_every_link_to_its_source(client, world):
    x = _proposed("chain")
    r = _attest(x["lineage"])
    assert r.status_code == 200, r.text
    obl, det, att = r.json()["obligation"], r.json()["determination"], r.json()["attestation"]
    d = _dossier(obl["lineage_id"]).json()
    # Obligation -> the adopted determination revision and the attestation that adopted it (same lineage).
    assert (d["obligation"]["determination_revision_id"], d["obligation"]["attestation_id"]) == (det["revision_id"], att["attestation_id"])
    assert (d["determination"]["state"], d["determination"]["outcome"]) == ("ADOPTED", "APPLIES")
    assert att["target_revision_id"] in {z.revision_id for z in _determinations(x["lineage"])}
    # -> the evaluation it rests on, its exact fact revisions and rule version, which replays.
    ev = client.get(f"/api/applicability/evaluations/{det['evaluation_id']}", headers=H(CCO)).json()
    assert ev["replay"]["status"] == "REPRODUCED" and ev["evaluation_id"] == x["ev"]["evaluation_id"]
    assert sorted(f["fact_assertion_revision_id"] for f in ev["input_bundle"]["fact_inputs"]) == sorted(S["facts"][x["sid"]])
    # -> the signed release, the exact requirement version, its provisions and the source snapshot fingerprint.
    p = d["regulatory_provenance"]
    assert (p["binding_basis"], p["rule_version_id"], p["content_release_id"], p["integrity"]["status"]) == (
        "RELEASE_BOUND", ev["rule_version_id"], S["pub"]["content_release"]["content_release_id"], "INTACT")
    assert _bound(d) == [(S["rv"]["requirement_version_id"], S["source"]["fingerprint"], True, True,
                          tuple(sorted(q["provision_id"] for q in S["rv"]["provisions"])))]
    assert _verified(p["content_release_id"])
    # -> the satisfaction expression (from the definition's template) and its control mapping.
    assert [e["version"] for e in d["expression_versions"]] == [1] and [c["control_key"] for c in d["controls"]] == [CONTROL]
    assert d["decision_history"]["integrity"]["status"] == "INTACT"


def test_the_chain_stays_explainable_through_correction_supersession_and_lifecycle(client, world):
    """A correction release, the bound requirement version superseded, then closure and reopening: each revision keeps
    the exact chain its decision used, readable now and as of its record sequence; nothing earlier is rewritten."""
    rv = _requirement(client, f"ROC-{TAG}-CHAIN", "2025-01-01", "keep chain register")  # its own key: superseded below
    odef = _definition(rv)
    first = _publish(client, odef, rv)
    x = _proposed("chain-life", odef)
    obl = _attest(x["lineage"]).json()["obligation"]
    lin, adopted_seq = obl["lineage_id"], obl["record_from_seq"]
    before = _dossier(lin).json()
    chain0 = (before["regulatory_provenance"]["rule_version_id"], _bound(before))

    corr = _publish(client, odef, rv, "CORRECTION", "Chain correction")          # 1. the release is corrected
    rv2 = _requirement(client, rv["requirement_key"], "2026-01-01", "keep chain register v2")
    _publish(client, _definition(rv2, template=False), rv2)                       # 2. rv superseded by rv2
    assert _attest(x["lineage"], revision_id=_flag(x, "PAYMENT_INSTITUTION")).status_code == 200   # 3. closed
    assert _attest(x["lineage"], revision_id=_flag(x, "CREDIT_INSTITUTION")).status_code == 200    # 4. reopened
    revs = _obligations(x["sid"])
    assert [o.lifecycle for o in revs] == ["ADOPTED", "CLOSED", "ADOPTED"] and len({o.lineage_id for o in revs}) == 1

    # Each revision, read as of its own record sequence, names the decision and chain it was recorded with.
    at_adoption = _dossier(lin, adopted_seq).json()
    assert (at_adoption["regulatory_provenance"]["rule_version_id"], _bound(at_adoption)) == chain0
    assert at_adoption["regulatory_provenance"]["content_release"]["corrected_by_release_ids"] == []
    closed = _dossier(lin, revs[1].record_from_seq).json()
    assert closed["obligation"]["lifecycle"] == "CLOSED"
    assert (closed["regulatory_provenance"]["rule_version_id"], _bound(closed)) == chain0  # the origin adoption's chain
    assert closed["regulatory_provenance"]["content_release"]["corrected_by_release_ids"] == [corr["content_release"]["content_release_id"]]
    closure = closed["decision_history"]["revisions"][1]["closure"]
    assert closure["evaluation"]["rule_version_id"] == corr["rule_version"]["rule_version_id"]  # decided under the correction
    now = _dossier(lin).json()
    assert now["obligation"]["revision_id"] == revs[2].revision_id
    assert now["regulatory_provenance"]["rule_version_id"] == corr["rule_version"]["rule_version_id"]
    assert [b[0] for b in _bound(now)] == [rv["requirement_version_id"]]   # the bound version, superseded since
    for d in (at_adoption, closed, now):
        assert d["regulatory_provenance"]["integrity"]["status"] == "INTACT", d["regulatory_provenance"]["integrity"]
        assert d["decision_history"]["integrity"]["status"] == "INTACT", d["decision_history"]["integrity"]
        assert [e["version"] for e in d["expression_versions"]] == [1]
    # Nothing earlier was rewritten: the releases verify, every evaluation replays, the first revision is unchanged.
    assert _verified(first["content_release"]["content_release_id"]) and _verified(corr["content_release"]["content_release_id"])
    assert set(_replays(x["sid"])) == {"REPRODUCED"}
    assert (revs[0].determination_revision_id, revs[0].attestation_id) == (obl["determination_revision_id"], obl["attestation_id"])


def test_another_organisation_reaches_no_link_of_the_chain(client, world):
    x = _proposed("chain-tenant")
    obl = _attest(x["lineage"]).json()["obligation"]
    lin = obl["lineage_id"]
    start = datetime.fromisoformat(obl["valid_from"].replace("Z", "+00:00"))
    period = {"period_from": start.isoformat(), "period_to": (start + timedelta(days=1)).isoformat()}
    snap = client.post(f"/api/obligations/{lin}/snapshots", headers=H(CCO), json=period)
    assert snap.status_code == 200, snap.text
    # The assessment names the exact obligation revision and expression version (control mapping) it used, and reproduces.
    stored = client.get(f"/api/snapshots/{snap.json()['snapshot_id']}", headers=H(CCO)).json()
    [expr] = _dossier(lin).json()["expression_versions"]
    assert (stored["obligation_revision_id"], stored["expression_version_id"]) == (obl["revision_id"], expr["expression_version_id"])
    assert client.get(f"/api/snapshots/{stored['snapshot_id']}/reproduce", headers=H(CCO)).json()["status"] == "REPRODUCED"
    S["tenant_ids"] = {"lineage": lin, "lineage_det": x["lineage"], "snapshot": snap.json()["snapshot_id"],
                       "evaluation": x["ev"]["evaluation_id"], "sid": x["sid"], "attestation": obl["attestation_id"]}
    reads = [("GET", f"/api/obligations/{lin}", None), ("GET", f"/api/obligations/{lin}?as_of_record_seq=1", None),
             ("GET", f"/api/obligations/{lin}/expression-versions", None),
             ("GET", f"/api/snapshots/{snap.json()['snapshot_id']}", None),
             ("GET", f"/api/snapshots/{snap.json()['snapshot_id']}/reproduce", None),
             ("GET", f"/api/applicability/evaluations/{x['ev']['evaluation_id']}", None),
             ("POST", f"/api/obligations/{lin}/preview", period)]
    writes = [("POST", f"/api/obligations/{lin}/snapshots", period),
              ("POST", f"/api/obligations/{lin}/occurrences", {"window_from": period["period_from"], "due_at": period["period_to"]}),
              ("POST", f"/api/obligations/{lin}/expression-versions", {"ast": {"node_type": "CONTROL", "node_id": "c",
                                                                               "control_key": CONTROL},
                                                                       "valid_from": period["period_from"], "rationale": "x"}),
              ("POST", f"/api/applicability/determinations/{x['lineage']}/attest", {"decision": "ADOPT", "authority_basis": BASIS}),
              ("POST", f"/api/applicability/determinations/{x['lineage']}/challenge", {"challenge_class": "FACT_WRONG", "detail": "x"})]
    before = db_fingerprint()
    leaks = []
    for method, path, body in reads + writes:
        r = client.request(method, path, headers=H(CCO_B), json=body)
        if r.status_code not in (403, 404) or lin in r.text and r.status_code != 404:
            leaks.append((method, path, r.status_code, r.text[:160]))
    assert not leaks, leaks
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ PostgreSQL 17
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # row-level security and concurrent transactions; reason required by the CI gate
    return True


def test_row_level_security_hides_every_chain_row_from_another_organisation(client, world, pg):
    """The same chain rows read directly under each organisation's database context: visible to their own, never to
    another (FORCE RLS on every table of the chain)."""
    ids = S.get("tenant_ids")
    if ids is None:
        test_another_organisation_reaches_no_link_of_the_chain(client, world)
        ids = S["tenant_ids"]
    from app.db import SessionLocal, set_principal
    probes = {"assurance_obligation_instance_revision": ("lineage_id", ids["lineage"]),
              "applicability_determination_revision": ("lineage_id", ids["lineage_det"]),
              "governance_attestation": ("attestation_id", ids["attestation"]),
              "applicability_evaluation": ("evaluation_id", ids["evaluation"]),
              "applicability_fact_assertion_revision": ("subject_id", ids["sid"]),
              "assurance_satisfaction_expression_version": ("obligation_lineage_id", ids["lineage"]),
              "assurance_snapshot": ("snapshot_id", ids["snapshot"])}

    def counts(tenant, actor):
        with SessionLocal() as db:
            set_principal(db, tenant, actor)
            out = {}
            for table, (col, value) in probes.items():
                out[table] = db.execute(text(f"SELECT count(*) FROM {table} WHERE {col} = :v"), {"v": value}).scalar()
            db.rollback()
            return out
    own, other = counts(TA, CCO), counts(TB, CCO_B)
    assert all(n > 0 for n in own.values()), own
    assert all(n == 0 for n in other.values()), other


def _race(monkeypatch, module, name, first, second):
    held, release = threading.Event(), threading.Event()
    real = getattr(module, name)

    def holding(*a, **k):
        if threading.current_thread().name == "A":
            held.set()
            assert release.wait(60)
        return real(*a, **k)
    monkeypatch.setattr(module, name, holding)
    out = {}

    def run(key, fn):
        try:
            out[key] = ("ok", fn())
        except HTTPException as e:
            out[key] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001 - reported by the assertions
            out[key] = ("error", repr(e))
    a = threading.Thread(target=run, args=("A", first), name="A")
    a.start()
    assert held.wait(60), "thread A never reached the critical section"
    b = threading.Thread(target=run, args=("B", second), name="B")
    b.start()
    b.join(1.5)
    waited = b.is_alive()
    release.set()
    a.join(90)
    b.join(90)
    return out, waited


def _engine_attest(lineage, expected_revision_id=None):
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.models import Actor
    from app.security import Ctx
    with SessionLocal() as db:
        set_principal(db, TA, CCO)
        r = appl.attest(db, ctx=Ctx(db=db, actor=db.get(Actor, CCO)), determination_lineage_id=lineage, decision="ADOPT",
                        authority_basis=BASIS, expected_revision_id=expected_revision_id)
        db.commit()
        return r["obligation"].lineage_id if r["obligation"] else None


def _router_publish(did, reason):
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.routers.rules import PublishIn, publish
    from app.security import Ctx
    with SessionLocal() as db:
        set_principal(db, None, RELEASE)
        db.execute(text("SET lock_timeout = '60s'"))
        res = publish(did, PublishIn(label=f"roc-race-{next(_n)}-{TAG}", changelog="race", correction_reason=reason),
                      Ctx(db=db, actor=db.get(Actor, RELEASE)))
        return res["rule_version"]["rule_version_id"], res["content_release"]["content_release_id"]


def _consistent(lineage) -> dict:
    """The obligation's provenance is the decision's own: rule version of the evaluation the adopted determination rests
    on, release of that rule version, intact chain and history."""
    d = _dossier(lineage).json()
    ev = S["client"].get(f"/api/applicability/evaluations/{d['determination']['evaluation_id']}", headers=H(CCO)).json()
    p = d["regulatory_provenance"]
    assert p["rule_version_id"] == ev["rule_version_id"], (p["rule_version_id"], ev["rule_version_id"])
    assert p["integrity"]["status"] == "INTACT" and d["decision_history"]["integrity"]["status"] == "INTACT"
    assert ev["replay"]["status"] == "REPRODUCED"
    return p


def test_a_correction_published_while_an_adoption_commits_leaves_consistent_provenance(client, world, pg, monkeypatch):
    """The adoption holds the decision scope; the correction waits for it, then re-evaluates the subject (same outcome):
    the obligation keeps the rule it was adopted under, and its provenance names the correction."""
    from app.engines import applicability as appl
    x = _proposed("race-adopt-first")
    did = _shadowed_draft(client, S["odef"], S["rv"], "CORRECTION")
    out, waited = _race(monkeypatch, appl, "_activate_obligation", lambda: _engine_attest(x["lineage"]),
                        lambda: _router_publish(did, "Race correction"))
    assert waited and out["A"][0] == "ok" and out["B"][0] == "ok", out
    p = _consistent(out["A"][1])
    assert p["rule_version_id"] == x["ev"]["rule_version_id"] != out["B"][1][0]
    assert out["B"][1][1] in p["content_release"]["corrected_by_release_ids"]


def test_an_adoption_waiting_on_a_correction_adopts_the_refreshed_decision_or_is_refused(client, world, pg, monkeypatch):
    """The correction holds the decision scope while it re-evaluates the subject; the adoption waits. Adopting the
    revision reviewed before the correction is refused (STALE_DETERMINATION_REVISION, APP §16); adopting the current one
    rests on the correction's evaluation, and the obligation's provenance names that rule."""
    from app.routers import rules as rules_router
    x = _proposed("race-correct-first")
    reviewed = [d.revision_id for d in _determinations(x["lineage"]) if d.record_to_seq is None][0]
    did = _shadowed_draft(client, S["odef"], S["rv"], "CORRECTION")
    out, waited = _race(monkeypatch, rules_router, "_reevaluate_targets", lambda: _router_publish(did, "Race correction 2"),
                        lambda: _engine_attest(x["lineage"], expected_revision_id=reviewed))
    assert waited and out["A"][0] == "ok", out
    assert out["B"][0] == 409 and out["B"][1]["error"] == "STALE_DETERMINATION_REVISION", out
    assert _obligations(x["sid"]) == []  # nothing adopted on the superseded review
    r = _attest(x["lineage"])
    assert r.status_code == 200, r.text
    p = _consistent(r.json()["obligation"]["lineage_id"])
    assert p["rule_version_id"] == out["A"][1][0] != x["ev"]["rule_version_id"]
