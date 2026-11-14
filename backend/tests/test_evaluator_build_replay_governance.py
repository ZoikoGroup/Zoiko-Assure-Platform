"""Evaluator build identity in historical replay and golden-vector evidence.

Controlling text:
  * ZA-APP-001 §14 "Determinism check: Same evaluator build + RuleVersion + EvaluationInputBundle hash MUST yield
    byte-equivalent canonical result/trace": a reproduction is only claimable under the recorded build; §13 "evaluation
    stores build hash"; §06 evaluation_key (the build is part of it); APP-REQ-003 (every evaluation identifies its build).
  * ZA-TIM-001 §06 "Replaying the stored frozen input bundle is the primary reproducibility path"; TIM-REQ-012.
  * ZA-RUL-001 §11 stage 9 "Run all mandatory vectors on exact evaluator build targeted for release", §17 /test "Results
    linked to draft revision/evaluator build", RUL-REQ-017, §23 G1 "Golden tests: ... target evaluator build recorded".

A build change is simulated as an approved change would be made: codes.EVALUATOR_BUILD names another build, and where a
different answer is needed the evaluator's operator semantics are patched for the duration of the replay. The build
identifier itself is never changed by this module (it stays za-applicability-evaluator:1.0.0).

Each test except the last fails on the implementation before this change; the last is a guard (no defect claimed).
Replay read-only, tenant isolation, same-build tampering, build-separated and legacy evaluation keys and concurrent
evaluation are covered by test_evaluation_replay_guards and test_evaluation_key_compliance and are not repeated here.
Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"EBR-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-ebr-{TAG.lower()}"
CCO = f"ebr-cco-{TAG.lower()}"
EU, TYPE = "entity.established_in_eu", "entity.financial_entity_type"
OTHER_BUILD = "za-applicability-evaluator:9.9.9-governance-probe"
S: dict = {}


def _ast(tag: str) -> dict:  # APPLIES iff established in the EU and a credit institution
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", EU, "BOOLEAN", True), pred(f"{tag}_2", TYPE, "ENUM", "CREDIT_INSTITUTION")]}


def _vectors() -> list[dict]:
    def v(vid, cls, eu, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {EU: eu, TYPE: "CREDIT_INSTITUTION"}, "expected_outcome": expected}
    return [v("P", "POSITIVE", True, "APPLIES"), v("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
            v("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]


def _draft(client) -> str:
    p, tag = S["rv"]["provisions"][0], f"b{secrets.token_hex(3)}"
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
        "valid_from": "2024-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    return d.json()["draft_id"]


def _released(client) -> str:
    did = _draft(client)
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, did):
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"ebr-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Build Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="EBR CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Build Act",
        "locator": f"OJ L {TAG}/ebr", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"EBR-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"EBR_{TAG}", title="Build obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    _publish(client, _released(client))
    return True


def _evaluated(client, name: str, *, eu: bool = True, outcome: str = "APPLIES") -> dict:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    for key, value in ((EU, eu), (TYPE, "CREDIT_INSTITUTION")):
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})
        assert r.status_code == 200, r.text
    ev = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert ev.status_code == 200 and ev.json()["outcome"] == outcome, ev.text
    return ev.json()


def _view(client, evaluation_id) -> dict:
    r = client.get(f"/api/applicability/evaluations/{evaluation_id}", headers=H(CCO))
    assert r.status_code == 200, r.text
    return r.json()


def _another_evaluator(monkeypatch, behaviour: str):
    """The evaluator of another build: its identifier, and (for this replay) operator semantics that answer differently."""
    from app import codes
    from app.engines import rules
    real = rules._apply

    def apply(op, v):
        if behaviour == "refusal":
            raise rules.RuleError("RUNTIME_RULE_INVARIANT_BREACH", "probe: this build refuses the recorded inputs")
        return not real(op, v)
    monkeypatch.setattr(rules, "_apply", apply)
    if behaviour != "same_build":
        monkeypatch.setattr(codes, "EVALUATOR_BUILD", OTHER_BUILD)


# ------------------------------------------------------------------------------------------------ replay across builds
def test_inputs_altered_under_another_build_are_never_reported_as_a_build_change(client, world, monkeypatch):
    """REDERIVED_BUNDLE compared the running build's bundle hash with the record's. The hash includes the build, so the
    check failed for every evaluation of another build and the status read "only the build differs" (BUILD_CHANGED),
    even when a historical fact value had been altered in place. The inputs are now compared under one build."""
    from app import codes
    from app.db import AdminSession
    from app.models import FactAssertion
    ev = _evaluated(client, "altered", eu=False, outcome="DOES_NOT_APPLY")
    used = {f["fact_definition"]: f["fact_assertion_revision_id"] for f in ev["input_bundle"]["fact_inputs"]}
    with AdminSession() as db:  # a value the outcome and trace do not depend on (ALL is decided by EU = false)
        db.get(FactAssertion, used[TYPE]).value_text = "PAYMENT_INSTITUTION"
        db.commit()
    monkeypatch.setattr(codes, "EVALUATOR_BUILD", OTHER_BUILD)
    replay = _view(client, ev["evaluation_id"])["replay"]
    assert replay["status"] == "NOT_REPRODUCED", replay
    assert set(replay["failed_checks"]) == {"EVALUATOR_BUILD", "REDERIVED_BUNDLE"}, replay


@pytest.mark.parametrize("behaviour", ["different_answer", "refusal"])
def test_another_builds_answer_on_intact_inputs_is_a_build_change_not_a_failed_reproduction(client, world, monkeypatch,
                                                                                           behaviour):
    """APP §14 guarantees the same result only under the same build. A newer build that answers differently (or refuses
    the inputs) for an evaluation recorded by another build, whose stored and re-derived inputs are intact, was
    NOT_REPRODUCED, the status of altered historical data. It is BUILD_CHANGED, with the difference still listed. Under
    the recorded build the same difference stays NOT_REPRODUCED. The record is unchanged and nothing is written."""
    ev = _evaluated(client, f"answer-{behaviour}")
    with monkeypatch.context() as m:  # same build, other semantics: the recorded build's own result is not reproduced
        _another_evaluator(m, "same_build")
        same = _view(client, ev["evaluation_id"])["replay"]
    assert same["status"] == "NOT_REPRODUCED" and "EVALUATOR_BUILD" not in same["failed_checks"], same
    before = db_fingerprint()
    _another_evaluator(monkeypatch, behaviour)
    view = _view(client, ev["evaluation_id"])
    replay = view["replay"]
    assert replay["status"] == "BUILD_CHANGED" and replay["matches"] is False, replay
    expected = {"EVALUATOR_BUILD", "OUTCOME", "TRACE"} if behaviour == "different_answer" else {"EVALUATOR_BUILD", "OUTCOME"}
    assert set(replay["failed_checks"]) == expected, replay
    if behaviour == "refusal":
        assert replay["error"] == "RUNTIME_RULE_INVARIANT_BREACH" and replay["reproduced_outcome"] is None
    assert (view["outcome"], view["evaluator_build"], view["input_bundle_hash"]) == \
        (ev["outcome"], ev["evaluator_build"], ev["input_bundle_hash"])
    assert db_fingerprint() == before


@pytest.mark.parametrize("recorded", ["", "za-applicability-evaluator:0.0.1-unrecorded"], ids=["missing", "other"])
def test_a_row_whose_build_disagrees_with_its_frozen_inputs_is_never_a_build_change(client, world, recorded):
    """An evaluation row naming no build, or a build other than the one its frozen (hash-protected) bundle names, was
    replayed as a genuine evaluation of another build (BUILD_CHANGED). Its evaluator cannot be established from the
    record, so it is an inconsistent record: NOT_REPRODUCED, the bundle no longer binds the row (BUNDLE_BINDS_RULE).
    The record is not changed."""
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation, new_id
    from app.platform import next_record_seq
    ev = _evaluated(client, f"row-{secrets.token_hex(2)}")
    with AdminSession() as db:  # inserted as another row (evaluations are append-only), below the API
        src = db.get(ApplicabilityEvaluation, ev["evaluation_id"])
        row = ApplicabilityEvaluation(**{c.key: getattr(src, c.key) for c in ApplicabilityEvaluation.__mapper__.column_attrs
                                         if c.key not in ("evaluation_id", "evaluation_key", "record_seq", "evaluator_build")},
                                      evaluation_id=new_id(), evaluation_key=secrets.token_hex(32),
                                      record_seq=next_record_seq(db), evaluator_build=recorded)
        db.add(row)
        db.commit()
        row_id = row.evaluation_id
    view = _view(client, row_id)
    replay = view["replay"]
    assert replay["status"] == "NOT_REPRODUCED", replay
    assert "BUNDLE_BINDS_RULE" in replay["failed_checks"] and view["evaluator_build"] == recorded, replay


# ------------------------------------------------------------------------------------------------ golden-vector evidence
def test_golden_vector_results_name_the_build_that_ran_them(client, world, monkeypatch):
    """The vector runner reported a fixed copy of the build identifier, so after a build change the new evaluator's
    results would have been attributed to the build before it (RUL §17: "Results linked to ... evaluator build")."""
    from app import codes
    from app.db import AdminSession
    from app.models import RuleDraft
    did = _draft(client)
    monkeypatch.setattr(codes, "EVALUATOR_BUILD", OTHER_BUILD)
    r = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR))
    assert r.status_code == 200 and r.json()["all_passed"], r.text
    assert r.json()["evaluator_build"] == OTHER_BUILD
    assert client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR)).status_code == 200
    with AdminSession() as db:
        assert db.get(RuleDraft, did).vector_report["evaluator_build"] == OTHER_BUILD


def test_a_release_records_the_evaluator_build_its_golden_vectors_passed_on(client, world, monkeypatch):
    """RUL-REQ-017 / §23 G1 "target evaluator build recorded": the signed release recorded how many vectors passed but
    not on which build, so a release could not be told apart from one validated by another evaluator. Runs after the
    replay tests: it publishes a newer version of this module's rule."""
    from app import codes
    from app.platform import canonical_json, sha256_hex
    did = _released(client)
    monkeypatch.setattr(codes, "EVALUATOR_BUILD", OTHER_BUILD)
    cr = _publish(client, did)["content_release"]
    assert cr["manifest"]["golden_vector_result"] == {"total": 3, "passed": 3, "evaluator_build": OTHER_BUILD}
    assert sha256_hex(canonical_json(cr["manifest"])) == cr["manifest_hash"]  # inside the signed manifest


def test_the_seeded_golden_vector_fixtures_still_pass_on_the_running_build(client, world):
    """Guard (passes before and after this change): every golden vector of the seeded published rules, run against the
    released canonical AST on the evaluator as it is now, still passes and is attributed to the running build. A future
    change to evaluator semantics that breaks released evidence fails here instead of passing silently."""
    from app import codes
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.engines import rules
    from app.models import ContentRelease, RuleVersion
    labels = ("CR-2026-AIA-001", "CR-2026-AIA-002", "CR-2026-DORA-001", "CR-2026-DORA-002")
    with AdminSession() as db:
        fact_defs, sets = appl.load_fact_defs(db), appl.load_sets(db)
        releases = db.execute(select(ContentRelease).where(ContentRelease.label.in_(labels))).scalars().all()
        assert sorted(cr.label for cr in releases) == sorted(labels)
        for cr in releases:
            [entry] = cr.manifest["rule_versions"]
            rv = db.get(RuleVersion, entry["rule_version_id"])
            vr = rules.run_golden_vectors(rv.canonical_ast, rv.golden_vectors, fact_defs, sets)
            assert vr["all_passed"] and vr["total"] == cr.manifest["golden_vector_result"]["total"], (cr.label, vr)
            assert vr["evaluator_build"] == codes.EVALUATOR_BUILD
