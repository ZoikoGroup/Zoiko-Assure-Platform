"""Control mappings and SatisfactionExpression versions (engines/satisfaction.py; routers/assurance.py).

Requirements exercised: ZA-STA-001 §07 (CONTROL leaf = "exact ControlImplementation or control selector"; required
metadata; "Expression versioning": a change is a new version with valid/record time; snapshots pin the exact version),
§08, §09, §16, §22 (Assurance Reviewer approves the design), STA-REQ-006/009/010/011/013/021/022; ZA-DOM-001 §7.1
(ControlMapping: expression leaf -> control implementation or control role, many-to-many, effective-dated), §9.1/§9.2
(bitemporal; a corrected mapping is a new revision and prior snapshots keep the old one), §13; ZA-TIM-001 §09/TIM-REQ-015;
ZA-API-001 API-REQ-008/014.

Every test builds its own organisation, controls and obligations; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-cm-{TAG}", f"tenant-cmb-{TAG}"
ENT, SUBJ, SYS = f"entity-cm-{TAG}", f"subj-cm-{TAG}", f"subj-cm-sys-{TAG}"
ENT_B, SUBJ_B = f"entity-cmb-{TAG}", f"subj-cmb-{TAG}"
OWNER, REVIEWER, CCO, B_CCO = f"cm-owner-{TAG}", f"cm-review-{TAG}", f"cm-cco-{TAG}", f"cmb-cco-{TAG}"
B_OWNER = f"cmb-owner-{TAG}"
INC, HUM, MON, DEV = "C_ICT_INCIDENT_PROCESS", "C_HUMAN_OVERSIGHT", "C_AI_MONITORING_LOGS", "C_DEVICE_ATTESTATION"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def org(client):
    """Organisation A (a legal-entity subject and a system subject of the same entity) and organisation B."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tid, eid, sid in ((TA, ENT, SUBJ), (TB, ENT_B, SUBJ_B)):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=eid, tenant_id=tid, legal_name=f"{tid} Bank AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"{tid} Bank AG"))
        db.add(Subject(subject_id=SYS, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name="Probe system"))
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (REVIEWER, TA, ["RISK_APPROVER"]), (CCO, TA, ["CCO"]),
                                (B_CCO, TB, ["CCO"]), (B_OWNER, TB, ["CONTROL_OWNER"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _control(client, key, subject=SUBJ, actor=OWNER, **policy):
    tests = policy.pop("tests", [{"name": f"{key} test"}])
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": key, "subject_id": subject, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "tests": tests, **policy})
    assert r.status_code == 200, r.text
    return r.json()["control_implementation_id"]


def _test_definition(ci_id):
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci_id)
                         .order_by(ControlTestPolicy.version.desc())).scalars().first()
        return db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                          ).scalars().first().test_definition_id


@pytest.fixture(scope="module")
def impls(client, org):
    """Control implementations: two for organisation A's legal entity, one for its system subject, one for B."""
    return {"inc": _control(client, INC), "hum": _control(client, HUM), "dev": _control(client, DEV),
            "sys_mon": _control(client, MON, subject=SYS),
            "b_inc": _control(client, INC, subject=SUBJ_B, actor=B_OWNER)}


def _obligation(versions, *, tenant=TA, entity=ENT, subject=SUBJ):
    """A probe obligation lineage on the seeded DORA definition with expression versions [(version, ast, from, to)]."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=tenant, entity_id=entity, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        for version, ast, vf, vt in versions:
            db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=version,
                                                 ast=ast, ast_hash=digest(ast), rationale="probe", valid_from=vf,
                                                 valid_to=vt, record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def C(key, nid=None, impl=None, rationale="Required by the obligation.", **extra):
    n = {"node_type": "CONTROL", "node_id": nid or f"n_{key.lower()}", "control_key": key, **extra}
    if rationale is not None:
        n["rationale"] = rationale
    if impl:
        n["control_implementation_id"] = impl
    return n


def N(kind, *kids, nid="root", rationale="Obligation logic.", **extra):
    n = {"node_type": kind, "node_id": nid, "children": list(kids), **extra}
    if rationale is not None:
        n["rationale"] = rationale
    return n


def _url(lineage):
    return f"/api/obligations/{lineage}/expression-versions"


def _create(client, lineage, ast, *, valid_from="2026-01-01T00:00:00Z", actor=REVIEWER, **extra):
    return client.post(_url(lineage), headers=H(actor), json={"ast": ast, "valid_from": valid_from,
                                                             "rationale": "Mapping reviewed by assurance.", **extra})


def _versions(lineage):
    from app.db import AdminSession
    from app.models import SatisfactionExpressionVersion
    with AdminSession() as db:
        return db.execute(select(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == lineage)
            .order_by(SatisfactionExpressionVersion.version)).scalars().all()


def _preview(client, lineage, start="2026-01-01", end="2026-02-01", **extra):
    return client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO),
                       json={"period_from": f"{start}T00:00:00Z", "period_to": f"{end}T00:00:00Z", **extra})


def _run(client, ci_id, result, start="2026-01-01", end="2026-02-01"):
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": _test_definition(ci_id), "occurred_at": f"{start}T01:00:00Z",
        "window_from": f"{start}T00:00:00Z", "window_to": f"{end}T00:00:00Z", "result": result, "source_system": "probe"})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------------ authoring
def test_a_valid_version_creates_maps_and_retires_controls_with_history(client, impls):
    from app.db import AdminSession
    from app.models import AuditEvent
    lineage = _obligation([(1, {"node_type": "ALL", "node_id": "root", "children": [C(INC), C(DEV)]}, T0, None)])
    listed = client.get(_url(lineage), headers=H(CCO)).json()
    assert [(v["version"], [(m["control_key"], m["binding"]) for m in v["control_mappings"]]) for v in listed] == \
        [(1, [(INC, "SELECTOR"), (DEV, "SELECTOR")])]
    # Version 2: INC re-bound to an exact implementation, DEV retired, HUM added (selector).
    r = _create(client, lineage, N("ALL", C(INC, impl=impls["inc"]), C(HUM)), valid_from="2026-03-01T00:00:00Z")
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["record_seq"] > listed[0]["record_seq"]
    assert [(m["control_key"], m["binding"], m["control_implementation_id"]) for m in v2["control_mappings"]] == \
        [(INC, "EXACT", impls["inc"]), (HUM, "SELECTOR", None)]
    with AdminSession() as db:
        [a] = db.execute(select(AuditEvent).where(AuditEvent.action == "satisfaction_expression.version_created",
                                                  AuditEvent.object_id == v2["expression_version_id"])).scalars().all()
    assert a.actor_id == REVIEWER and a.tenant_id == TA
    assert f"added ['{HUM}']" in a.reason and f"retired ['{DEV}']" in a.reason and f"rebound ['{INC}']" in a.reason
    # The earlier version is untouched; the dossier shows both with their mappings.
    v1 = _versions(lineage)[0]
    assert v1.version == 1 and v1.ast["children"][1]["control_key"] == DEV
    dossier = client.get(f"/api/obligations/{lineage}", headers=H(CCO)).json()
    assert [v["version"] for v in dossier["expression_versions"]] == [1, 2]
    assert dossier["expression_versions"][1]["control_mappings"][0]["binding"] == "EXACT"


@pytest.mark.parametrize("make, code", [
    (lambda i: N("ALL", C("NO_SUCH_CONTROL")), "UNKNOWN_CONTROL_DEFINITION"),
    (lambda i: N("ALL", C(INC, impl="no-such-implementation")), "CONTROL_MAPPING_INVALID"),
    (lambda i: N("ALL", C(HUM, impl=i["inc"])), "CONTROL_MAPPING_INVALID"),         # implements another control
    (lambda i: N("ALL", C(MON, impl=i["sys_mon"])), "CONTROL_MAPPING_INVALID"),     # outside the subject scope
    (lambda i: N("ALL", C(INC, impl=i["b_inc"])), "CONTROL_MAPPING_INVALID"),       # another organisation's
    (lambda i: N("ALL", C(INC, nid="a"), C(INC, nid="b")), "DUPLICATE_CONTROL_LEAF"),
    (lambda i: N("ANY", C(INC, impl=i["inc"]), N("ALL", C(INC, nid="again"), C(HUM), nid="inner")),
     "CONTRADICTORY_CONTROL_MAPPING"),                                               # exact and selector at once
    (lambda i: N("ALL", C(INC, nid="x"), C(HUM, nid="x")), "DUPLICATE_NODE_ID"),
    (lambda i: N("ALL", C(INC, rationale=None)), "RATIONALE_REQUIRED"),
    (lambda i: N("ANY", C(INC), C(HUM), rationale=None), "RATIONALE_REQUIRED"),
    (lambda i: N("ALL", C(INC, required=False)), "CONTROL_NOT_REQUIRED"),
    (lambda i: N("XOR", C(INC), C(HUM)), "EXPRESSION_STRUCTURE_INVALID"),             # unsupported operator
    (lambda i: N("ALL"), "EXPRESSION_STRUCTURE_INVALID"),                            # empty
    (lambda i: N("K_OF_N", C(INC), C(HUM), k=3), "EXPRESSION_STRUCTURE_INVALID"),
    (lambda i: N("K_OF_N", C(INC), C(HUM)), "EXPRESSION_STRUCTURE_INVALID"),         # k missing
    (lambda i: N("ALL", {"node_type": "CONTROL", "node_id": "m", "control_key": INC, "rationale": "r",
                         "control_implementation_id": 7}), "EXPRESSION_STRUCTURE_INVALID"),  # malformed binding
    (lambda i: N("ALL", {"node_type": "COMPENSATION", "node_id": "c", "compensation_id": "forged",
                         "primary": C(INC), "compensating": N("ALL", C(DEV), nid="cc")}), "COMPENSATION_NODE_INVALID"),
])
def test_invalid_expressions_and_mappings_are_refused_and_write_nothing(client, impls, make, code):
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    r = _create(client, lineage, make(impls))
    assert r.status_code == 422, r.text
    assert code in [p["code"] for p in r.json()["detail"]["problems"]], r.json()
    assert [v.version for v in _versions(lineage)] == [1]


@pytest.mark.parametrize("body", [
    {"ast": "ALL", "valid_from": "2026-01-01T00:00:00Z", "rationale": "x"},                        # malformed AST
    {"ast": N("ALL", C(INC)), "valid_from": "2026-01-01T00:00:00Z", "rationale": ""},              # no rationale
    {"ast": N("ALL", C(INC)), "valid_from": "2026-01-01T00:00:00Z", "rationale": "x", "version": 9},  # mass assignment
    {"ast": N("ALL", C(INC)), "rationale": "x"},                                                   # no valid_from
])
def test_malformed_requests_are_refused(client, impls, body):
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    assert client.post(_url(lineage), headers=H(REVIEWER), json=body).status_code == 422
    assert len(_versions(lineage)) == 1


def test_empty_period_and_unchanged_versions_are_refused(client, impls):
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    r = _create(client, lineage, N("ALL", C(INC)), valid_to="2026-01-01T00:00:00Z")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "EXPRESSION_PERIOD_EMPTY"
    assert _create(client, lineage, N("ALL", C(INC, impl=impls["inc"]))).status_code == 201
    again = _create(client, lineage, N("ALL", C(INC, impl=impls["inc"])))
    assert again.status_code == 409 and again.json()["detail"]["error"] == "EXPRESSION_UNCHANGED"
    assert [v.version for v in _versions(lineage)] == [1, 2]


def test_authorisation_and_tenant_isolation(client, impls):
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    body = {"ast": N("ALL", C(INC, impl=impls["inc"])), "valid_from": "2026-01-01T00:00:00Z", "rationale": "x"}
    assert client.post(_url(lineage), json=body).status_code == 401
    assert client.post(_url(lineage), headers=H(OWNER), json=body).status_code == 403   # proposes input only (STA §22)
    assert client.post(_url(lineage), headers=H("actor-agent"), json=body).status_code == 403
    assert client.post(_url(lineage), headers=H(B_CCO), json=body).status_code in (403, 404)  # other organisation
    assert client.get(_url(lineage), headers=H(B_CCO)).status_code in (403, 404)
    assert client.get(_url("no-such-obligation"), headers=H(CCO)).status_code == 404
    assert len(_versions(lineage)) == 1
    assert client.post(_url(lineage), headers=H(CCO), json=body).status_code == 201       # CCO holds assurance_review


# ------------------------------------------------------------------------------------------------ evaluation path
def _leaf(resp, key):
    assert resp.status_code == 200, resp.text
    return [l for s in resp.json()["segments"] for l in s["leaf_evaluations"] if l["control_key"] == key]


def test_exact_mappings_drive_evaluation_without_false_positives(client, impls):
    """Mapping -> expression -> governed test results -> control evaluation -> obligation satisfaction."""
    lineage = _obligation([(1, N("ALL", C(INC, impl=impls["inc"])), T0, None)], subject=SUBJ)
    # No test result yet: missing proof, never satisfied.
    p = _preview(client, lineage)
    [leaf] = _leaf(p, INC)
    assert (leaf["binding"], leaf["control_implementation_id"], leaf["control_state"]) == ("EXACT", impls["inc"], "NOT_EVALUATED")
    assert p.json()["state"] == "NOT_PROVEN"
    # A PASS without evidence is not proof (MISSING_EVIDENCE).
    _run(client, impls["inc"], "PASS")
    p = _preview(client, lineage)
    [leaf] = _leaf(p, INC)
    assert leaf["control_state"] == "NOT_EVALUATED" and "MISSING_EVIDENCE" in leaf["reason_codes"]
    assert p.json()["state"] == "NOT_PROVEN"
    # A FAIL is decisive.
    _run(client, impls["inc"], "FAIL")
    p = _preview(client, lineage)
    assert _leaf(p, INC)[0]["control_state"] == "FAILING" and p.json()["state"] == "FAILING"


def test_an_invalid_control_configuration_behind_a_mapping_is_an_error_not_a_state(client, impls):
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    from app.platform import next_record_seq
    ci = _control(client, MON)
    with AdminSession() as db:  # a policy version written before the API checks existed
        pol = ControlTestPolicy(tenant_id=TA, control_implementation_id=ci, aggregation_mode="K_OF_N_TESTS",
                                k_threshold=-5, automation_requirement="PREFERRED", version=2, record_seq=next_record_seq(db))
        db.add(pol)
        db.flush()
        db.add(TestDefinition(tenant_id=TA, policy_version_id=pol.policy_version_id, name="t", source_class="CONNECTOR"))
        db.commit()
    lineage = _obligation([(1, N("ALL", C(MON, impl=ci)), T0, None)])
    p = _preview(client, lineage)
    assert p.status_code == 409 and p.json()["detail"]["code"] == "CONTROL_TEST_POLICY_INVALID"


def test_a_stored_mapping_that_is_no_longer_valid_is_an_error_not_a_state(client, impls):
    for bad in (impls["b_inc"], impls["sys_mon"], "no-such-implementation"):
        key = MON if bad == impls["sys_mon"] else INC
        lineage = _obligation([(1, N("ALL", C(key, impl=bad)), T0, None)])
        p = _preview(client, lineage)
        assert p.status_code == 409 and p.json()["detail"]["code"] == "CONTROL_MAPPING_INVALID", (bad, p.text)


# ------------------------------------------------------------------------------------------------ history
def _segments(resp):
    assert resp.status_code == 200, resp.text
    return [(s["segment_range"][0][:10], s["segment_range"][1][:10], s["expression_version_id"],
             [(l["control_key"], l.get("binding")) for l in s["leaf_evaluations"]]) for s in resp.json()["segments"]]


def test_version_changes_apply_from_their_valid_time_and_history_is_reproducible(client, impls):
    from app.db import AdminSession
    from app.models import AssuranceSnapshot
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    v2 = _create(client, lineage, N("ALL", C(INC, impl=impls["inc"]), C(HUM)), valid_from="2026-03-01T00:00:00Z").json()
    v1_id = _versions(lineage)[0].expression_version_id
    segs = _segments(_preview(client, lineage, "2026-01-01", "2026-05-01"))
    assert {(e, end <= "2026-03-01") for _, end, e, _ in segs} == {(v1_id, True), (v2["expression_version_id"], False)}
    # Freeze a snapshot, then change the mappings again, retroactively.
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json={
        "period_from": "2026-01-01T00:00:00Z", "period_to": "2026-05-01T00:00:00Z"})
    assert snap.status_code == 200, snap.text
    snap = snap.json()
    v3 = _create(client, lineage, N("ALL", C(DEV)), valid_from="2026-01-01T00:00:00Z")
    assert v3.status_code == 201
    # Current knowledge uses v3 everywhere (highest version in force)...
    assert {e for _, _, e, _ in _segments(_preview(client, lineage, "2026-01-01", "2026-05-01"))} == \
        {v3.json()["expression_version_id"]}
    # ...but what was known at the snapshot's cutoff reproduces the frozen result exactly.
    then = _preview(client, lineage, "2026-01-01", "2026-05-01", record_cutoff_seq=snap["record_cutoff_seq"])
    assert _segments(then) == [(s["segment_range"][0][:10], s["segment_range"][1][:10], s["expression_version_id"],
                                [(l["control_key"], l.get("binding")) for l in s["leaf_evaluations"]])
                               for s in snap["segments"]]
    assert then.json()["dependency_digest"] == snap["dependency_digest"]
    with AdminSession() as db:  # the stored snapshot never changed
        stored = db.get(AssuranceSnapshot, snap["snapshot_id"])
        assert stored.snapshot_hash == snap["snapshot_hash"]
        assert {s["expression_version_id"] for s in stored.segments} == {v1_id, v2["expression_version_id"]}


def test_an_approved_compensation_is_carried_forward_only_unchanged(client, impls):
    lineage = _obligation([(1, N("ALL", C(INC), C(HUM)), T0, None)])
    c = client.post("/api/compensations", headers=H(OWNER), json={
        "obligation_lineage_id": lineage, "primary_control_key": INC, "compensating_control_keys": [DEV],
        "equivalence_rationale": "Device attestation addresses the same objective for this scope.",
        "valid_from": "2026-01-01T00:00:00Z"})
    assert c.status_code == 200, c.text
    assert client.post(f"/api/compensations/{c.json()['compensation_id']}/decide", headers=H(REVIEWER),
                       json={"decision": "APPROVE", "reason": "ok"}).status_code == 200
    latest = _versions(lineage)[-1].ast
    comp = next(n for n in latest["children"] if n["node_type"] == "COMPENSATION")
    carried = N("ALL", comp, C(HUM, impl=impls["hum"]))  # mapping change, compensation carried as approved
    assert _create(client, lineage, carried).status_code == 201
    altered = N("ALL", {**comp, "compensating": {"node_type": "ALL", "node_id": "cc", "children": [C(MON)]}}, C(HUM))
    r = _create(client, lineage, altered)
    assert r.status_code == 422 and "COMPENSATION_NODE_INVALID" in [p["code"] for p in r.json()["detail"]["problems"]]


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _race(*asts, lineage):
    from app.db import SessionLocal, set_principal
    from app.engines import satisfaction
    from app.models import Actor
    from app.security import Ctx
    barrier, results = threading.Barrier(len(asts)), []

    def worker(ast):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, REVIEWER)
                ctx = Ctx(db=db, actor=db.get(Actor, REVIEWER))
                barrier.wait()
                e = satisfaction.create_version(ctx, lineage_id=lineage, ast=ast, valid_from=T0, valid_to=None,
                                                rationale="concurrent change")
                db.commit()
                results.append(e.version)
        except HTTPException as e:
            results.append(e.status_code)
        except Exception as e:  # noqa: BLE001
            results.append(repr(e))
    threads = [threading.Thread(target=worker, args=(a,)) for a in asts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return sorted(results, key=str)


def test_concurrent_mapping_changes_get_distinct_versions_and_identical_ones_one(client, impls, pg):
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    assert _race(N("ALL", C(INC, impl=impls["inc"])), N("ALL", C(HUM)), lineage=lineage) == [2, 3]
    assert [v.version for v in _versions(lineage)] == [1, 2, 3]
    same = N("ALL", C(DEV))
    assert _race(same, same, lineage=lineage) == [4, 409]  # one new version, one EXPRESSION_UNCHANGED (sorted as text)
    assert [v.version for v in _versions(lineage)] == [1, 2, 3, 4]


def test_an_evaluation_concurrent_with_a_mapping_change_uses_one_consistent_version(client, impls, pg):
    """A preview reads every expression version recorded by its cutoff: a version committed later never changes it."""
    lineage = _obligation([(1, N("ALL", C(INC)), T0, None)])
    before = _preview(client, lineage)
    cutoff = before.json()["record_cutoff_seq"]
    results = []

    def change():
        results.append(_create(client, lineage, N("ALL", C(HUM, impl=impls["hum"]))).status_code)

    def evaluate():
        results.append(_segments(_preview(client, lineage, record_cutoff_seq=cutoff)))
    threads = [threading.Thread(target=change), threading.Thread(target=evaluate)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert 201 in results
    assert [r for r in results if isinstance(r, list)] == [_segments(before)]
    assert _segments(_preview(client, lineage, record_cutoff_seq=cutoff)) == _segments(before)
