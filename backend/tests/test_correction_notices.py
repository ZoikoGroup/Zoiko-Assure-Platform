"""Customer correction notices (app/engines/corrections.py; POST /api/rules/drafts/{id}/publish for change class
CORRECTION; GET /api/catalog/releases and GET /api/catalog/releases/{id}/impact).

Requirements exercised: PRD-001 §3.1 and FR-WRK-003 (impacted tenants receive a signed correction notice and scoped
impact report), §12 ("through the content release mechanism"); ZA-GLS-001 "Correction Notice" (identifies affected
release(s); must trigger scoped impact analysis); ZA-RUL-001 change class CORRECTION and WF-RUL-03 (explicit correction
reason); ZA-TIM-001 WF-TIM-03 (impact counts and materiality; as known then); ZA-WRK-001 WRK-REQ-043 (corrections preserve
the original release and customer decision history); ZA-EVT-001 content_release.corrected (CONTENT_RELEASE profile);
ZA-API-001 §17 (correction flags; tenant-authorized impact summary, no other-tenant detail); ZA-DAT-001 DAT-REQ-012.

Every test builds its own instrument, source, requirement version, obligation definition and probe organisations; the
seeded catalogue is only read.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"CN-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
ORGS = {k: (f"tenant-cn{k.lower()}-{TAG.lower()}", f"subj-cn{k.lower()}-{TAG.lower()}", f"cn{k.lower()}-cco-{TAG.lower()}")
        for k in ("A", "B", "C")}
S = {}  # module state shared by the ordered tests below


def _ast(tag, entity_type):
    def pred(nid, fact, typ, value, sm):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [sm],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True, f"sm_{tag}_1"),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", entity_type, f"sm_{tag}_2")]}


def _vectors(entity_type):
    return [{"vector_id": "CN-POS", "vector_class": "POSITIVE", "description": "in scope", "effective_at": "2025-06-01",
             "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": entity_type},
             "expected_outcome": "APPLIES"},
            {"vector_id": "CN-NEG", "vector_class": "NEGATIVE", "description": "outside the EU", "effective_at": "2025-06-01",
             "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": entity_type},
             "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "CN-UNK", "vector_class": "UNKNOWN", "description": "type unknown", "effective_at": "2025-06-01",
             "facts": {"entity.established_in_eu": True,
                       "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
             "expected_outcome": "UNCERTAIN"}]


def _draft(client, odef_id, tag, change_class, entity_type="CREDIT_INSTITUTION"):
    """A VERIFIED, shadow-run draft ready for publication."""
    rv, src = S["rv"], S["source"]
    p0, p1 = rv["provisions"][0], rv["provisions"][1]
    m = lambda sid, p: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                        "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                        "source_fingerprint": src["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag, entity_type),
        "golden_vectors": _vectors(entity_type), "valid_from": "2025-01-01", "change_class": change_class,
        "source_mappings": [m(f"sm_{tag}_root", p0), m(f"sm_{tag}_1", p0), m(f"sm_{tag}_2", p1)]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, did, label, actor=RELEASE, **extra):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(actor),
                       json={"label": label, "changelog": label, **extra})


def _odef(provision_ids, suffix):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"CN_{TAG}_{suffix}", title=f"Correction notice probe {suffix}",
                                 instrument_id=inst.instrument_id, provision_ids=provision_ids, subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _count(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalar()


def _impact(client, release_id, actor, **params):
    return client.get(f"/api/catalog/releases/{release_id}/impact", headers=H(actor), params=params)


@pytest.fixture(scope="module")
def world(client):
    """A released obligation (r1) adopted by organisations A and B; B also holds a snapshot and a signed package of it;
    organisation C has never evaluated it."""
    from app.db import CELL_ID, AdminSession
    from app.models import AssuranceSnapshot, EvidencePackage, Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Correction notice probe instrument", jurisdiction="EU",
                          authority="Probe Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid, sid, cco in ORGS.values():
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=f"entity-{tid}", tenant_id=tid, legal_name=f"{tid} Bank AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=f"entity-{tid}", subject_type="LEGAL_ENTITY",
                           display_name=f"{tid} Bank AG"))
            add_principal(db, actor_id=cco, tenant_id=tid, actor_type="HUMAN", display_name=cco, roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Probe Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Probe regulation (synthetic)", "locator": f"OJ L {TAG}/cn", "instrument_identifier": INSTR,
        "raw_text": "Financial entities shall maintain a register of ICT third-party arrangements and review it."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"CN-{TAG}-REQ",
        "title": "Register of ICT third-party arrangements", "locator": "Art. 4", "valid_from": "2025-01-01",
        "provisions": [
            {"locator": "Art. 4(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain a register",
             "regulated_object": "ICT third-party arrangements", "timing": "continuous",
             "normalized_meaning": "Maintain a register.", "evidence_expectation": "Register"},
            {"locator": "Art. 4(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "review the register",
             "regulated_object": "ICT third-party register", "timing": "annually",
             "normalized_meaning": "Review the register.", "evidence_expectation": "Review record"}]})
    assert rv.status_code in (200, 201), rv.text
    S["source"], S["rv"] = src.json(), rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["provision_ids"] = [p["provision_id"] for p in S["rv"]["provisions"]]
    S["odef"] = _odef(S["provision_ids"], "MAIN")
    r1 = _publish(client, _draft(client, S["odef"], "r1", "EXECUTABLE_OUTCOME"), f"cn-{TAG}-r1")
    assert r1.status_code == 200, r1.text
    S["r1"], S["rv1"] = r1.json()["content_release"], r1.json()["rule_version"]
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for k in ("A", "B"):
        tid, sid, cco = ORGS[k]
        for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
            assert client.post(f"/api/subjects/{sid}/facts", headers=H(cco),
                               json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
        ev = client.post("/api/applicability/evaluations", headers=H(cco),
                         json={"subject_id": sid, "obligation_definition_id": S["odef"]}).json()
        assert ev["outcome"] == "APPLIES" and ev["rule_version_id"] == S["rv1"]["rule_version_id"], ev
        assert client.post(f"/api/applicability/determinations/{ev['current_determination']['lineage_id']}/attest",
                           headers=H(cco), json={"decision": "ADOPT", "authority_basis": "Board delegation"}).status_code == 200
        [obl] = [o for o in client.get("/api/obligations", headers=H(cco)).json()
                 if o["obligation_definition_id"] == S["odef"]]
        S[f"eval_{k}"], S[f"obl_{k}"] = ev["evaluation_id"], obl
    tid, sid, _ = ORGS["B"]
    with AdminSession() as db:  # B's assurance artefacts over the affected obligation
        snap = AssuranceSnapshot(tenant_id=tid, entity_id=f"entity-{tid}", obligation_lineage_id=S["obl_B"]["lineage_id"],
                                 obligation_revision_id=S["obl_B"]["revision_id"], period_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 period_to=datetime(2026, 4, 1, tzinfo=timezone.utc), record_cutoff_seq=1, perspective="AS_KNOWN_THEN",
                                 expression_version_id="probe", rollup_engine_version="probe", state="PROVEN", segments=[],
                                 dependency_manifest={}, dependency_digest="0" * 64, snapshot_hash="0" * 64, created_by="probe")
        db.add(snap)
        db.flush()
        pkg = EvidencePackage(tenant_id=tid, entity_id=f"entity-{tid}", period_from=snap.period_from, period_to=snap.period_to,
                              record_cutoff_seq=1, scope={"evidence_ids": [], "snapshot_ids": [snap.snapshot_id]},
                              manifest_hash="1" * 64, signer_key_id="probe", file_ref="probe", verification_report={},
                              created_by="probe")
        db.add(pkg)
        db.commit()
        S["snapshot_B"], S["package_B"] = snap.snapshot_id, pkg.package_id
    return True


# ------------------------------------------------------------------------------------------------ publication
def test_invalid_correction_publications_are_refused_before_anything_is_written(client, world):
    from app.db import AdminSession
    from app.models import ContentRelease, RuleDraft
    S["correction_draft"] = did = _draft(client, S["odef"], "c1", "CORRECTION", "PAYMENT_INSTITUTION")
    before = _count(ContentRelease)
    for extra, status, code in (({}, 422, "CORRECTION_REASON_REQUIRED"),
                                ({"correction_reason": "   "}, 422, "CORRECTION_REASON_REQUIRED")):
        r = _publish(client, did, f"cn-{TAG}-bad", **extra)
        assert r.status_code == status and r.json()["detail"]["error"] == code, r.text
    assert _publish(client, did, f"cn-{TAG}-bad", correction_reason="x" * 2001).status_code == 422
    # A reason belongs only to a correction.
    other = _draft(client, _odef(S["provision_ids"], "PLAIN"), "p1", "EXECUTABLE_OUTCOME")
    r = _publish(client, other, f"cn-{TAG}-plain", correction_reason="not a correction")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "CORRECTION_REASON_NOT_APPLICABLE"
    # A correction must correct an existing published release of the same obligation definition.
    orphan = _draft(client, _odef(S["provision_ids"], "ORPHAN"), "o1", "CORRECTION")
    r = _publish(client, orphan, f"cn-{TAG}-orphan", correction_reason="nothing to correct")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "CORRECTION_TARGET_MISSING"
    assert _publish(client, "no-such-draft", f"cn-{TAG}-none", correction_reason="x").status_code == 404
    assert _count(ContentRelease) == before
    with AdminSession() as db:
        assert {db.get(RuleDraft, d).status for d in (did, other, orphan)} == {"VERIFIED"}
    for actor in ("actor-cco", AUTHOR, ORGS["A"][2]):  # only the release authority publishes, never an organisation
        assert _publish(client, did, f"cn-{TAG}-who", actor, correction_reason="x").status_code == 403, actor


def test_a_failure_mid_publication_leaves_no_notice_and_the_draft_publishable(client, world, monkeypatch):
    from app.models import AuditEvent, ContentRelease, OutboxEvent
    from app.routers import rules as rules_router
    real = rules_router.emit

    def broken(db, **kw):
        if kw.get("event_type") == "content_release.corrected":
            raise RuntimeError("outbox write failed")
        return real(db, **kw)
    monkeypatch.setattr(rules_router, "emit", broken)
    with pytest.raises(RuntimeError):
        _publish(client, S["correction_draft"], f"cn-{TAG}-rollback", correction_reason="rollback probe")
    monkeypatch.setattr(rules_router, "emit", real)
    assert _count(ContentRelease, label=f"cn-{TAG}-rollback") == 0
    assert _count(OutboxEvent, event_type="content_release.corrected", aggregate_id=S["r1"]["content_release_id"]) == 0
    assert _count(AuditEvent, action="content.publish", object_id=S["r1"]["content_release_id"]) == 1  # r1's only
    assert client.get(f"/api/rules/drafts/{S['correction_draft']}", headers=H(AUTHOR)).json()["status"] == "VERIFIED"


def test_a_correction_is_a_new_signed_release_and_the_corrected_release_is_untouched(client, world):
    from app.db import AdminSession
    from app.models import AuditEvent, ContentRelease, OutboxEvent
    with AdminSession() as db:
        r1_before = {k: v for k, v in json.loads(json.dumps(
            {c.key: getattr(db.get(ContentRelease, S["r1"]["content_release_id"]), c.key)
             for c in ContentRelease.__table__.columns}, default=str)).items()}
    reason = "Article 4 applies to payment institutions, not credit institutions; the released rule named the wrong type."
    r = _publish(client, S["correction_draft"], f"cn-{TAG}-r2", correction_reason=f"  {reason}  ")
    assert r.status_code == 200, r.text
    r2, rv2 = r.json()["content_release"], r.json()["rule_version"]
    S["r2"], S["rv2"] = r2, rv2
    assert r2["correction_of_release_id"] == S["r1"]["content_release_id"]
    assert r2["manifest"]["correction"] == {"corrects_release_id": S["r1"]["content_release_id"],
                                            "corrects_rule_version_id": S["rv1"]["rule_version_id"], "reason": reason}
    check = client.get(f"/api/catalog/releases/{r2['content_release_id']}/verify", headers=H(AUTHOR)).json()
    assert check["manifest_hash_ok"] and check["integrity"] == "INTACT"  # the reason is inside the signed manifest
    with AdminSession() as db:
        r1_after = json.loads(json.dumps({c.key: getattr(db.get(ContentRelease, S["r1"]["content_release_id"]), c.key)
                                          for c in ContentRelease.__table__.columns}, default=str))
        assert r1_after == r1_before  # WRK-REQ-043: the corrected release is not rewritten
        [ev] = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "content_release.corrected",
                                                    OutboxEvent.aggregate_id == r2["content_release_id"])).scalars().all()
        assert ev.tenant_id is None and ev.record_seq == r2["cell_received_seq"]
        assert ev.payload == {"content_release_id": r2["content_release_id"], "manifest_hash": r2["manifest_hash"],
                              "rule_version_ids": [rv2["rule_version_id"]],
                              "requirement_version_ids": [S["rv"]["requirement_version_id"]],
                              "valid_from": "2025-01-01", "valid_to": None,
                              "corrects": S["r1"]["content_release_id"], "supersedes": S["rv1"]["rule_version_id"]}
        [a] = db.execute(select(AuditEvent).where(AuditEvent.action == "content.publish",
                                                  AuditEvent.object_id == r2["content_release_id"])).scalars().all()
        assert a.actor_id == RELEASE and reason in a.reason and S["r1"]["content_release_id"] in a.reason
    retry = _publish(client, S["correction_draft"], f"cn-{TAG}-r2", correction_reason=reason)
    assert retry.status_code == 409  # a retry never issues a second notice
    assert _count(ContentRelease, label=f"cn-{TAG}-r2") == 1
    flags = {x["content_release_id"]: x for x in client.get("/api/catalog/releases", headers=H(ORGS["C"][2])).json()}
    assert (flags[r2["content_release_id"]]["is_correction"], flags[r2["content_release_id"]]["corrects_release_id"]) == (
        True, S["r1"]["content_release_id"])
    assert flags[r2["content_release_id"]]["correction"]["reason"] == reason
    assert flags[S["r1"]["content_release_id"]]["is_correction"] is False
    assert flags[S["r1"]["content_release_id"]]["corrected_by_release_ids"] == [r2["content_release_id"]]


# ------------------------------------------------------------------------------------------------ impact
def test_each_affected_organisation_gets_its_own_confirmed_and_potential_impact(client, world):
    rid = S["r2"]["content_release_id"]
    for k in ("A", "B"):
        tid, sid, cco = ORGS[k]
        rep = _impact(client, rid, cco).json()
        assert rep["is_correction"] and rep["correction"]["reason_recorded"] is True
        assert rep["scope"]["rule_version_ids"] == [S["rv2"]["rule_version_id"]]
        assert [r["requirement_version_id"] for r in rep["scope"]["requirement_versions"]] == [S["rv"]["requirement_version_id"]]
        t = rep["tenant_impact"]
        assert t["impact"] == "CONFIRMED" and t["dependencies_complete"] is True and t["state_changed"] is False
        assert t["prior_rule_version_ids"] == [S["rv1"]["rule_version_id"]]
        assert [e["evaluation_id"] for e in t["confirmed"]["evaluations"]] == [S[f"eval_{k}"]]
        [subj] = t["subjects"]
        assert (subj["subject_id"], subj["prior_outcome"], subj["corrected_outcome"], subj["outcome_changed"]) == (
            sid, "APPLIES", "DOES_NOT_APPLY", True)
        [det] = t["confirmed"]["determinations"]
        # Reported, not acted on: the adoption stands (flagged for review by the existing re-evaluation), so a human
        # decides; the obligation is still ADOPTED.
        assert (det["state"], det["outcome"], det["needs_review"], det["material_change"]) == ("ADOPTED", "APPLIES", True, True)
        [obl] = t["confirmed"]["obligations"]
        assert (obl["lineage_id"], obl["lifecycle"]) == (S[f"obl_{k}"]["lineage_id"], "ADOPTED")
        assert t["counts"]["material_changes"] == 1
    b = _impact(client, rid, ORGS["B"][2]).json()["tenant_impact"]["potential"]
    assert [s["snapshot_id"] for s in b["snapshots"]] == [S["snapshot_B"]]
    assert [p["package_id"] for p in b["packages"]] == [S["package_B"]]
    a = _impact(client, rid, ORGS["A"][2]).json()["tenant_impact"]["potential"]
    assert a["snapshots"] == [] and a["packages"] == []
    c = _impact(client, rid, ORGS["C"][2]).json()["tenant_impact"]
    assert c["impact"] == "NONE_RECORDED" and c["counts"]["evaluations"] == 0  # never "unaffected"
    obls = [o for o in client.get("/api/obligations", headers=H(ORGS["A"][2])).json() if o["obligation_definition_id"] == S["odef"]]
    assert [(o["lineage_id"], o["lifecycle"]) for o in obls] == [(S["obl_A"]["lineage_id"], "ADOPTED")]


def test_no_organisation_sees_another_organisations_impact(client, world):
    rid = S["r2"]["content_release_id"]
    a = json.dumps(_impact(client, rid, ORGS["A"][2]).json())
    b = json.dumps(_impact(client, rid, ORGS["B"][2]).json())
    for secret in (ORGS["B"][0], ORGS["B"][1], S["eval_B"], S["obl_B"]["lineage_id"], S["snapshot_B"], S["package_B"]):
        assert secret not in a
    for secret in (ORGS["A"][0], ORGS["A"][1], S["eval_A"], S["obl_A"]["lineage_id"]):
        assert secret not in b
    staff = _impact(client, rid, RELEASE)  # content/platform staff: the global notice, no tenant data (INV-14)
    assert staff.status_code == 200 and staff.json()["tenant_impact"] is None and staff.json()["is_correction"]
    assert client.get(f"/api/catalog/releases/{rid}/impact").status_code == 401
    assert _impact(client, "no-such-release", ORGS["A"][2]).status_code == 404


def test_a_historical_report_is_reproducible_and_says_what_was_not_yet_known(client, world):
    rid, cco = S["r2"]["content_release_id"], ORGS["A"][2]
    at_release = _impact(client, rid, cco, as_of_record_seq=S["r2"]["cell_received_seq"]).json()["tenant_impact"]
    # At the release's own record time the re-evaluation was not yet recorded: impact stays CONFIRMED (the adoption rests
    # on corrected content) but whether its outcome changes is unknown, and the report says so.
    assert at_release["impact"] == "CONFIRMED" and at_release["dependencies_complete"] is False
    assert {p["code"] for p in at_release["problems"]} == {"RE_EVALUATION_NOT_RECORDED"}
    assert at_release["subjects"][0]["outcome_changed"] is None
    assert at_release["confirmed"]["determinations"][0]["material_change"] is None
    now = _impact(client, rid, cco).json()["tenant_impact"]
    seq = now["as_of_record_seq"]
    # Later records do not change a report taken as known at an earlier record sequence.
    _, sid, _ = ORGS["A"]
    assert client.post("/api/applicability/evaluations", headers=H(cco),
                       json={"subject_id": sid, "obligation_definition_id": S["odef"]}).status_code == 200
    again = _impact(client, rid, cco, as_of_record_seq=seq).json()["tenant_impact"]
    assert again == now
    for bad, code in ((10 ** 15, "RECORD_SEQ_IN_FUTURE"), (S["r2"]["cell_received_seq"] - 1, "RECORD_SEQ_BEFORE_RELEASE")):
        r = _impact(client, rid, cco, as_of_record_seq=bad)
        assert r.status_code == 422 and r.json()["detail"]["error"] == code
    assert _impact(client, rid, cco, as_of_record_seq=-1).status_code == 422


def test_missing_dependency_information_is_never_reported_as_no_impact(client, world):
    from app.db import AdminSession
    from app.models import ContentRelease
    with AdminSession() as db:  # a seeded release, published before releases recorded their requirement binding
        seeded = next(r for r in db.execute(select(ContentRelease)).scalars()
                      if "requirement_versions" not in (r.manifest or {}))
    rep = _impact(client, seeded.content_release_id, ORGS["C"][2]).json()
    assert {p["code"] for p in rep["scope_problems"]} >= {"REQUIREMENT_BINDING_NOT_RECORDED"}
    t = rep["tenant_impact"]
    assert t["impact"] == "INCOMPLETE" and t["dependencies_complete"] is False


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and row locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def test_concurrent_publication_of_one_correction_issues_one_notice(client, world, pg):
    from app.db import SessionLocal, set_principal
    from app.models import Actor, ContentRelease
    from app.routers import rules as rules_router
    from app.security import Ctx
    did = _draft(client, S["odef"], "c2", "CORRECTION")  # corrects r2 back to credit institutions
    label = f"cn-{TAG}-race"
    barrier, out = threading.Barrier(2), []

    def worker():
        try:
            with SessionLocal() as db:
                set_principal(db, None, RELEASE)
                ctx = Ctx(db=db, actor=db.get(Actor, RELEASE))
                barrier.wait()
                rules_router.publish(did, rules_router.PublishIn(label=label, changelog=label,
                                                                 correction_reason="race probe"), ctx)
                out.append(200)
        except HTTPException as e:
            out.append(e.status_code)
        except Exception as e:  # noqa: BLE001
            out.append(repr(e))
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert sorted(out, key=str) == [200, 409], out
    assert _count(ContentRelease, label=label) == 1
    from app.db import AdminSession
    with AdminSession() as db:
        [r3] = db.execute(select(ContentRelease).where(ContentRelease.label == label)).scalars().all()
        assert r3.correction_of_release_id == S["r2"]["content_release_id"]
