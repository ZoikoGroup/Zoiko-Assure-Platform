"""Missing-fact resolution view: GET /fact-requests/{request_id} (read-only).

Controlling text:
  * ZA-APP-001 §08 resolution wireframe: "Why Assure needs this fact: Requirement/provision, rule branch, effective date,
    current result = UNCERTAIN"; "Fact definition: Canonical name, expected type/domain, acceptable sources, sensitivity
    and why it is material"; "Current candidates: Existing assertions, provenance, validity, conflicts, expiry;
    sensitive values masked by role"; "Preview ... do not promise a specific outcome before evaluation".
  * ZA-APP-001 §16 WF-APP-02 "Missing facts: Open FactRequests with why-needed explanation"; "Conflicts: Show all
    admissible candidate assertions and why engine cannot choose".
  * ZA-APP-001 §04 fact resolution steps 1-6 (policy filter, expiry, conflict -> UNKNOWN / FACT_CONFLICT, no "latest
    record wins"); ZA-TIM-001 TIM-REQ-006/017 (explicit valid instant, echoed watermark).
  * QA-REQ-019 (cross-tenant reads), IAM-REQ-009/010 (server-resolved entity scope).

Pinned here: the view explains each linked evaluation from what it recorded (outcome, effective date, rule version,
materiality, the unknown reason its frozen bundle holds, the trace branches to the fact and the provisions mapped on
them) and shows, at one watermark, how the resolution algorithm resolves the fact for that evaluation's valid instant:
missing, conflicting candidates or the resolved assertion, with redacted values masked. The branch materiality is the
evaluator's own rule. It writes nothing, predicts nothing, and is scoped like the fact-request register.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from conftest import H, add_principal

TAG = secrets.token_hex(3)
TA, TB = f"tenant-frv-a-{TAG}", f"tenant-frv-b-{TAG}"
CCO_A, CCO_B, AUDITOR_A, TSA_A, ENTITY_READER = (f"frv-{r}-{TAG}" for r in ("cco-a", "cco-b", "aud-a", "tsa-a", "ent"))
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"FRV-{TAG.upper()}-REG"
F, P = (f"frv{TAG}.{n}" for n in ("flag", "secret"))
A, B = (f"FRV_{TAG.upper()}_{n}" for n in "AB")
NOW = datetime.now(timezone.utc).replace(microsecond=0)
REDACTED = "••• (redacted)"
S: dict = {}

TOP_KEYS = {"fact_request", "subject", "fact_definition", "needed_by", "record_watermark"}
FACT_DEFINITION_KEYS = {"fact_definition_id", "key", "label", "data_type", "enum_domain", "subject_type", "sensitivity",
                        "allowed_provenance", "manual_attestation_policy", "expiry_days", "conflict_policy",
                        "owner_role"}
NEEDED_BY_KEYS = {"evaluation_id", "record_seq", "subject_id", "effective_at", "record_cutoff_seq", "outcome",
                  "obligation_definition", "rule_version", "material", "consumed", "rule_branches", "source_mappings",
                  "current_resolution"}
RESOLUTION_KEYS = {"valid_at", "known_seq", "known", "reason", "assertion", "candidates", "rejected"}


def ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def _iso(d: datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------------------------------------------------ fixture
def _publish(client, key: str, fact: str, requirement: dict, src: dict) -> dict:
    """ObligationDefinition `key` whose rule is the single predicate EQ(fact, True); returns its IDs and node IDs."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=key, title=f"Request {key}", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in requirement["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = f"frv{secrets.token_hex(3)}", requirement["provisions"][0]
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
                    json={"label": f"frv-{secrets.token_hex(4)}", "changelog": "fact request probe"})
    assert r.status_code == 200, r.text
    return {"odef": odef, "pred": f"{t}_p", "fact_ref": f"{t}_pf", "mapping": maps[0]}


def _write(client, actor, sid, key, value, valid_from):
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(actor),
                    json={"fact_key": key, "value": value, "valid_from": _iso(valid_from)})
    assert r.status_code == 200, r.text
    [revision] = r.json()["revisions"]  # a first assertion over an open valid range: one new revision
    return revision


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


def _evaluate(client, actor, sid, odef, at: datetime) -> dict:
    r = client.post("/api/applicability/evaluations", headers=H(actor),
                    json={"subject_id": sid, "obligation_definition_id": odef, "effective_at": _iso(at)})
    assert r.status_code == 200, r.text
    return r.json()


def _request_id(actor, sid, key) -> str:
    from app.db import AdminSession
    from app.models import FactDefinition, FactRequest
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == key)).scalars().one()
        return db.execute(select(FactRequest.request_id).where(
            FactRequest.subject_id == sid, FactRequest.fact_definition_id == fd.fact_definition_id)).scalar_one()


@pytest.fixture(scope="module")
def world(client):
    """Organisation A: `one` (entity e-one) has no F and is evaluated for A at NOW and 30 days ago (one request, two
    linked evaluations); `two` (entity e-two) holds two disagreeing F assertions and two disagreeing values of the
    RESTRICTED_PERSONAL P (a conflict request for each). Organisation B: `other` with a missing F, for isolation."""
    from app.db import CELL_ID, AdminSession
    from app.models import FactDefinition, Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for t in (TA, TB):
            db.add(Tenant(tenant_id=t, legal_name=f"{t} Group", contracting_region="EU"))
        for key, sens in ((F, "CUSTOMER_INTERNAL"), (P, "RESTRICTED_PERSONAL")):
            db.add(FactDefinition(key=key, label=f"label {key}", data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                                  sensitivity=sens, manual_attestation_policy="ALLOWED"))
        db.flush()
        for name, tenant in (("one", TA), ("two", TA), ("three", TA), ("other", TB)):
            sid, eid = f"s-frv-{name}-{TAG}", f"e-frv-{name}-{TAG}"
            db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"FRV {name} Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.flush()
            db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"FRV {name} Ltd"))
            S[name], S[f"entity_{name}"] = sid, eid
        for aid, tenant, roles in ((CCO_A, TA, ["CCO"]), (CCO_B, TB, ["CCO"]), (AUDITOR_A, TA, ["EXTERNAL_AUDITOR"]),
                                   (TSA_A, TA, ["TENANT_SECURITY_ADMIN"]), (ENTITY_READER, TA, [])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.add(Instrument(identifier=INSTR, title="Request Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    r = client.post("/api/role-assignments", headers=H(TSA_A), json={
        "principal_id": ENTITY_READER, "role_code": "INTERNAL_AUDIT", "scope_type": "LEGAL_ENTITY",
        "scope_id": S["entity_one"], "reason": "fact request entity probe"})
    assert r.status_code == 201, r.text
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Request Act",
        "locator": f"OJ L {TAG}/frv", "instrument_identifier": INSTR, "raw_text": "Entities shall file a return."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"FRV-{TAG}-REQ",
        "title": "Return requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "file return",
             "regulated_object": "return", "timing": "annual", "normalized_meaning": "File a return.",
             "evidence_expectation": "Return"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["rule"] = {k: _publish(client, k, fact, rv.json(), src.json()) for k, fact in ((A, F), (B, P))}
    # one: F missing, evaluated twice (the second evaluation links to the same open request).
    S["one_evals"] = [_evaluate(client, CCO_A, S["one"], S["rule"][A]["odef"], at) for at in (NOW, ago(30))]
    # two: F and P each held by two disagreeing current assertions.
    S["two_f"] = [_write(client, CCO_A, S["two"], F, True, ago(400))["revision_id"]]
    S["two_f"].append(_second_source(S["two"], F, False))
    S["two_p"] = [_write(client, CCO_A, S["two"], P, True, ago(400))["revision_id"]]
    S["two_p"].append(_second_source(S["two"], P, False))
    S["two_f_eval"] = _evaluate(client, CCO_A, S["two"], S["rule"][A]["odef"], NOW)
    S["two_p_eval"] = _evaluate(client, CCO_A, S["two"], S["rule"][B]["odef"], NOW)
    S["other_eval"] = _evaluate(client, CCO_B, S["other"], S["rule"][A]["odef"], NOW)
    # three: F missing when evaluated at NOW and 30 days ago (one request).
    S["three_evals"] = [_evaluate(client, CCO_A, S["three"], S["rule"][A]["odef"], at) for at in (NOW, ago(30))]
    for e in (*S["one_evals"], S["two_f_eval"], S["two_p_eval"], S["other_eval"], *S["three_evals"]):
        assert e["outcome"] == "UNCERTAIN", e
    S["req"] = {"one": _request_id(CCO_A, S["one"], F), "two_f": _request_id(CCO_A, S["two"], F),
                "two_p": _request_id(CCO_A, S["two"], P), "other": _request_id(CCO_B, S["other"], F),
                "three": _request_id(CCO_A, S["three"], F)}
    return True


def _get(client, rid, actor=CCO_A):
    return client.get(f"/api/fact-requests/{rid}", headers=H(actor))


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _stored_request(rid) -> dict:
    from app.db import AdminSession
    from app.models import FactRequest
    from app.platform import to_dict
    with AdminSession() as db:
        return to_dict(db.get(FactRequest, rid))


def _branch(key: str, rule: dict, reason: str) -> list[dict]:
    """The one branch a single-predicate rule records for its fact."""
    return [{"path": [{"node_id": rule["pred"], "node_type": "PREDICATE", "result": "UNKNOWN", "operator": "EQ",
                       "reason_code": reason, "source_mappings": [rule["mapping"]["source_mapping_id"]]},
                      {"node_id": rule["fact_ref"], "node_type": "FACT_REF", "result": "UNKNOWN", "reason_code": reason,
                       "fact": key}],
             "material": True}]


def _mapping(rule: dict) -> list[dict]:
    m = rule["mapping"]
    return [{k: m[k] for k in ("source_mapping_id", "requirement_version_id", "provision_id",
                                "authoritative_reference")}]


# ------------------------------------------------------------------------------------------------------------ why needed
def test_a_missing_fact_is_explained_by_every_evaluation_that_needs_it(client, world):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation, FactDefinition, RuleVersion
    body = _ok(_get(client, S["req"]["one"]))
    assert set(body) == TOP_KEYS
    assert body["fact_request"] == _stored_request(S["req"]["one"])
    assert body["fact_request"]["status"] == "OPEN" and body["fact_request"]["reason_code"] == "MISSING_REQUIRED_FACT"
    assert body["subject"] == {"subject_id": S["one"], "display_name": "FRV one Ltd", "subject_type": "LEGAL_ENTITY",
                               "entity_id": S["entity_one"]}
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == F)).scalars().one()
        assert body["fact_definition"] == {k: getattr(fd, k) for k in FACT_DEFINITION_KEYS}
        evs = {e.evaluation_id: e for e in db.execute(select(ApplicabilityEvaluation).where(
            ApplicabilityEvaluation.evaluation_id.in_([e["evaluation_id"] for e in S["one_evals"]]))).scalars()}
        rv = db.get(RuleVersion, next(iter(evs.values())).rule_version_id)
        rule_version = {"rule_version_id": rv.rule_version_id, "version": rv.version,
                        "content_release_id": rv.content_release_id}
    wm = body["record_watermark"]
    assert set(wm) == {"cell_id", "record_seq", "perspective"} and wm["perspective"] == "CURRENT"
    # Both linked evaluations, in record order, each explained from what it recorded.
    assert [n["evaluation_id"] for n in body["needed_by"]] == [e["evaluation_id"] for e in S["one_evals"]]
    for item, requested in zip(body["needed_by"], (NOW, ago(30))):
        assert set(item) == NEEDED_BY_KEYS
        e = evs[item["evaluation_id"]]
        assert (item["record_seq"], item["subject_id"], item["record_cutoff_seq"], item["outcome"]) == (
            e.record_seq, S["one"], e.record_cutoff_seq, "UNCERTAIN")
        assert item["effective_at"] == _iso(requested)
        assert item["obligation_definition"] == {"obligation_definition_id": S["rule"][A]["odef"], "key": A,
                                                 "title": f"Request {A}", "material": True}
        assert item["rule_version"] == rule_version
        assert item["material"] is True and item["consumed"] == {"unknown_reason": "MISSING_REQUIRED_FACT"}
        assert item["rule_branches"] == _branch(F, S["rule"][A], "MISSING_REQUIRED_FACT")
        assert item["source_mappings"] == _mapping(S["rule"][A])
        # Today's resolution for that evaluation's valid instant, at the response's one watermark: still missing.
        assert item["current_resolution"] == {"valid_at": _iso(requested), "known_seq": wm["record_seq"],
                                              "known": False, "reason": "MISSING_REQUIRED_FACT", "assertion": None,
                                              "candidates": [], "rejected": []}


def test_conflicting_candidates_are_listed_and_the_engine_chooses_none(client, world):
    body = _ok(_get(client, S["req"]["two_f"]))
    assert body["fact_request"]["reason_code"] == "FACT_CONFLICT"
    assert body["fact_definition"]["conflict_policy"] == "ERROR_TO_UNKNOWN"
    [item] = body["needed_by"]
    assert item["evaluation_id"] == S["two_f_eval"]["evaluation_id"]
    assert item["consumed"] == {"unknown_reason": "FACT_CONFLICT"}
    assert item["rule_branches"] == _branch(F, S["rule"][A], "FACT_CONFLICT")
    res = item["current_resolution"]
    assert set(res) == RESOLUTION_KEYS
    assert (res["known"], res["reason"], res["assertion"], res["rejected"]) == (False, "FACT_CONFLICT", None, [])
    # Every admissible candidate, each with its own value, provenance and validity (none chosen, latest included).
    assert sorted(c["revision_id"] for c in res["candidates"]) == sorted(S["two_f"])
    values = {c["revision_id"]: c["value"] for c in res["candidates"]}
    assert values == {S["two_f"][0]: True, S["two_f"][1]: False}
    for c in res["candidates"]:
        assert c["fact_key"] == F and c["provenance_class"] and c["valid_from"] and c["record_to_seq"] is None


def test_redacted_candidates_and_branches_carry_no_value(client, world):
    body = _ok(_get(client, S["req"]["two_p"]))
    assert body["fact_definition"]["sensitivity"] == "RESTRICTED_PERSONAL"
    [item] = body["needed_by"]
    res = item["current_resolution"]
    assert res["reason"] == "FACT_CONFLICT" and sorted(c["revision_id"] for c in res["candidates"]) == sorted(S["two_p"])
    for c in res["candidates"]:
        assert c["value"] == REDACTED
        assert all(c[k] is None for k in c if k.startswith("value_") and k != "value_type"), c
    for branch in item["rule_branches"]:
        for step in branch["path"]:
            assert "display_value" not in step and "value" not in step


def test_a_recorded_fact_shows_resolved_while_the_recorded_explanation_stays(client, world):
    before = _ok(_get(client, S["req"]["one"]))
    written = _write(client, CCO_A, S["one"], F, True, ago(400))  # re-runs the dependents and resolves the request
    after = _ok(_get(client, S["req"]["one"]))
    assert after["fact_request"]["status"] == "RESOLVED" and after["fact_request"] == _stored_request(S["req"]["one"])
    assert after["record_watermark"]["record_seq"] > before["record_watermark"]["record_seq"]
    for old, new in zip(before["needed_by"], after["needed_by"]):
        # What each evaluation recorded is unchanged; only today's resolution moves.
        assert {k: v for k, v in old.items() if k != "current_resolution"} == \
            {k: v for k, v in new.items() if k != "current_resolution"}
        res = new["current_resolution"]
        assert (res["known"], res["reason"], res["candidates"], res["rejected"]) == (True, None, [], [])
        assert res["assertion"]["revision_id"] == written["revision_id"] and res["assertion"]["value"] is True
        assert res["known_seq"] == after["record_watermark"]["record_seq"]


def test_each_linked_evaluation_is_resolved_at_its_own_valid_instant(client, world):
    """F recorded for [400 days ago, 10 days ago) only: today's resolution answers each evaluation for the instant it
    needed, so the evaluation of 30 days ago resolves and those of now remain missing. The request row is shown as
    stored, whatever its status."""
    written = _write_interval(client, S["three"], ago(400), ago(10))
    body = _ok(_get(client, S["req"]["three"]))
    assert body["fact_request"] == _stored_request(S["req"]["three"])
    assert {S["three_evals"][0]["evaluation_id"], S["three_evals"][1]["evaluation_id"]} <= {
        n["evaluation_id"] for n in body["needed_by"]}
    for item in body["needed_by"]:
        res = item["current_resolution"]
        assert res["valid_at"] == item["effective_at"]
        if item["effective_at"] == _iso(ago(30)):
            assert (res["known"], res["assertion"]["revision_id"]) == (True, written)
        else:
            assert item["effective_at"] >= _iso(ago(10))
            assert (res["known"], res["reason"], res["assertion"]) == (False, "MISSING_REQUIRED_FACT", None)


def _write_interval(client, sid, valid_from, valid_to) -> str:
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO_A), json={
        "fact_key": F, "value": True, "valid_from": _iso(valid_from), "valid_to": _iso(valid_to)})
    assert r.status_code == 200, r.text
    [revision] = r.json()["revisions"]
    return revision["revision_id"]


def test_linked_evaluations_that_cannot_be_found_are_listed_as_missing(client, world):
    from app.db import AdminSession
    from app.models import FactRequest
    rid = S["req"]["two_p"]
    original = _stored_request(rid)["linked_evaluation_ids"]
    foreign = S["other_eval"]["evaluation_id"]  # another organisation's evaluation: never shown
    with AdminSession() as db:
        db.execute(update(FactRequest).where(FactRequest.request_id == rid)
                   .values(linked_evaluation_ids=[*original, "no-such-evaluation", foreign]))
        db.commit()
    try:
        body = _ok(_get(client, rid))
        assert [n["evaluation_id"] for n in body["needed_by"]] == [*original, "no-such-evaluation", foreign]
        assert body["needed_by"][1:] == [{"evaluation_id": "no-such-evaluation", "missing": True},
                                         {"evaluation_id": foreign, "missing": True}]
    finally:
        with AdminSession() as db:
            db.execute(update(FactRequest).where(FactRequest.request_id == rid).values(linked_evaluation_ids=original))
            db.commit()


# ------------------------------------------------------------------------------------------------------------ scope
def test_another_organisations_request_is_not_found(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    assert _get(client, S["req"]["one"], CCO_B).status_code == 404
    assert _get(client, S["req"]["other"], CCO_A).status_code == 404
    assert _get(client, "no-such-request", CCO_A).status_code == 404
    theirs = _ok(_get(client, S["req"]["other"], CCO_B))
    assert [n["evaluation_id"] for n in theirs["needed_by"]] == [S["other_eval"]["evaluation_id"]]
    assert db_fingerprint() == before


def test_an_entity_scoped_reader_reaches_its_own_entity_only(client, world):
    assert _ok(_get(client, S["req"]["one"], ENTITY_READER))["subject"]["entity_id"] == S["entity_one"]
    assert _get(client, S["req"]["two_f"], ENTITY_READER).status_code == 404


def test_read_authorization_is_the_fact_request_registers(client, world):
    from test_authorization import db_fingerprint, denials
    before = db_fingerprint()
    for actor in (AUDITOR_A, TSA_A):  # outside READ: the external auditor allowlist and a role administrator
        n = denials(actor)
        assert _get(client, S["req"]["one"], actor).status_code == 403, actor
        assert denials(actor) == n + 1
    assert client.get(f"/api/fact-requests/{S['req']['one']}").status_code == 401
    assert db_fingerprint() == before


def test_reads_write_nothing(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    for rid in S["req"].values():
        actor = CCO_B if rid == S["req"]["other"] else CCO_A
        _ok(_get(client, rid, actor))
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------------------ branches
def _tree(kind: str, *children) -> dict:
    return {"node_type": kind, "node_id": f"{kind.lower()}_{len(children)}_{secrets.token_hex(2)}",
            "children": list(children)}


def _eq(fact: str, nid: str) -> dict:
    return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{nid}c", "type": "BOOLEAN", "value": True}]}


@pytest.mark.parametrize("ast, facts", [
    (_tree("ALL", _eq("f", "a"), _eq("g", "b")), {}),                                         # both material
    (_tree("ANY", _eq("f", "a"), _eq("g", "b")), {"g": True}),                                # f decided away
    (_tree("ALL", _eq("g", "b"), _eq("f", "a")), {"g": False}),                               # f short-circuited
    ({"node_type": "NOT", "node_id": "n", "child": _eq("f", "a")}, {}),                        # under NOT
    (_tree("ANY", _tree("ALL", _eq("f", "a"), _eq("g", "b")), _eq("f", "c")), {"g": True}),   # f on two branches
    (_tree("ALL", _tree("ANY", _eq("f", "a"), _eq("g", "b")), _eq("h", "c")), {"g": True}),   # f inside a TRUE ANY
])
def test_branch_materiality_is_the_evaluators_rule(ast, facts):
    """fact_ref_paths marks a branch material exactly when the evaluator lists the fact in missing_facts, and a fact
    with only non-material branches is one of its non_material_unknowns; known facts and constants show no value."""
    from app.engines import rules
    known = {k: {"known": True, "value": v, "revision_id": f"rev-{k}"} for k, v in facts.items()}
    out = rules.evaluate(ast, known, {k: "BOOLEAN" for k in ("f", "g", "h")}, {}, {})
    for key in ("f", "g", "h"):
        paths = rules.fact_ref_paths(out["trace"], key)
        assert any(p["material"] for p in paths) == (key in out["missing_facts"]), (key, paths)
        if key in out["non_material_unknowns"]:
            assert paths and not any(p["material"] for p in paths)
        for p in paths:
            assert p["path"][0]["node_id"] == ast["node_id"] and p["path"][-1]["fact"] == key
            assert all("display_value" not in step for step in p["path"])
    assert rules.fact_ref_paths(None, "f") == [] and rules.fact_ref_paths({"node_type": "ALL"}, "f") == []


def test_branch_materiality_agrees_with_every_stored_evaluation(client, world):
    """Over every recorded evaluation (the Customer Zero fixture and this module's): each missing fact has a material
    branch, each non-material unknown has branches and none material, and no other fact has a material branch."""
    from app.db import AdminSession
    from app.engines import rules
    from app.models import ApplicabilityEvaluation
    checked = 0
    with AdminSession() as db:
        for e in db.execute(select(ApplicabilityEvaluation)).scalars():
            facts = {n["fact"] for n in _fact_nodes(e.trace)}
            for key in facts:
                paths = rules.fact_ref_paths(e.trace, key)
                assert any(p["material"] for p in paths) == (key in (e.missing_facts or [])), (e.evaluation_id, key)
                if key in (e.non_material_unknowns or []):
                    assert paths and not any(p["material"] for p in paths)
            checked += 1
    assert checked >= 10


def _fact_nodes(node):
    if isinstance(node, dict):
        if node.get("node_type") == "FACT_REF":
            yield node
        for c in node.get("children") or []:
            yield from _fact_nodes(c)
