"""Historical applicability and obligation decision provenance: integrity verdicts on recorded history.

Builds one complete correction chain in its own organisations and checks that each historical decision still explains
itself from the records it wrote, and that anything substituted or missing is reported explicitly, never repaired,
replaced by a newer version or turned into a business state:

  Source -> RequirementVersion v1 -> Provisions -> release R1 -> RuleVersion 1 -> evaluation E1 (APPLIES)
    -> determination ADOPTED + attestation -> obligation ADOPTED -> SatisfactionExpression v1 -> snapshot
  expression v2 (control mapping change)
  correction release R2 (corrects R1) -> RuleVersion 2 -> evaluation E2 (DOES_NOT_APPLY) -> review flag
    -> attestation ADOPT (DOES_NOT_APPLY) -> obligation CLOSED
  RequirementVersion v2 (corrected text) released in R3 for another definition -> v1 SUPERSEDED

Requirements: ZA-APP-001 APP-REQ-003/006/011/012; ZA-TIM-001 TIM-REQ-006/007/012/015/017; ZA-RUL-001 RUL-REQ-009/026/027;
ZA-STA-001 STA-REQ-009/019/021/022; ZA-DAT-001 DAT-REQ-012; ZA-DOM-001 §1 canonical chain, §5 ObligationInstance.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from conftest import H, add_principal
from test_rule_integrity import _tampered

TAG = secrets.token_hex(3).upper()
INSTR = f"HPI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
ORGS = {k: (f"tenant-hpi{k.lower()}-{TAG.lower()}", f"subj-hpi{k.lower()}-{TAG.lower()}", f"hpi{k.lower()}-cco-{TAG.lower()}")
        for k in ("A", "B")}
S: dict = {}


def _ast(tag: str, entity_type: str) -> dict:
    def pred(nid, fact, typ, value, sm):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [sm],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True, f"sm_{tag}_1"),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", entity_type, f"sm_{tag}_2")]}


def _vectors(entity_type: str) -> list[dict]:
    facts = lambda eu, t: {"entity.established_in_eu": eu, "entity.financial_entity_type": t}
    return [{"vector_id": "HPI-POS", "vector_class": "POSITIVE", "description": "in scope", "effective_at": "2025-06-01",
             "facts": facts(True, entity_type), "expected_outcome": "APPLIES"},
            {"vector_id": "HPI-NEG", "vector_class": "NEGATIVE", "description": "outside the EU", "effective_at": "2025-06-01",
             "facts": facts(False, entity_type), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "HPI-UNK", "vector_class": "UNKNOWN", "description": "type unknown", "effective_at": "2025-06-01",
             "facts": facts(True, {"unknown": "MISSING_REQUIRED_FACT"}), "expected_outcome": "UNCERTAIN"}]


def _release(client, odef_id: str, rv: dict, tag: str, change_class: str, entity_type: str, label: str, **extra) -> dict:
    p0, p1 = rv["provisions"][0], rv["provisions"][1]
    m = lambda sid, p: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                        "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                        "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag, entity_type),
        "golden_vectors": _vectors(entity_type), "valid_from": "2025-01-01", "change_class": change_class,
        "source_mappings": [m(f"sm_{tag}_root", p0), m(f"sm_{tag}_1", p0), m(f"sm_{tag}_2", p1)]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": label, "changelog": label, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _requirement(client, key: str, valid_from: str, text_suffix: str) -> dict:
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
        "title": f"Contract register requirement{text_suffix}", "locator": "Art. 12", "valid_from": valid_from,
        "provisions": [
            {"locator": "Art. 12(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain register",
             "regulated_object": "contract register", "timing": "continuous",
             "normalized_meaning": f"Maintain register{text_suffix}.", "evidence_expectation": "Register"},
            {"locator": "Art. 12(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "assess risk",
             "regulated_object": "third party risk", "timing": "annually",
             "normalized_meaning": "Assess risk annually.", "evidence_expectation": "Assessment record"}]})
    assert rv.status_code in (200, 201), rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return rv


def _definition(key: str, provision_ids: list[str], template: dict) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=key, title=f"Historical provenance obligation {key}", instrument_id=inst.instrument_id,
                                 provision_ids=provision_ids, subject_type="LEGAL_ENTITY", coverage_mode="ENCODED",
                                 schedule_kind="CONTINUOUS", satisfaction_template=template, material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _evaluate(client, cco, sid, odef_id, **extra):
    r = client.post("/api/applicability/evaluations", headers=H(cco),
                    json={"subject_id": sid, "obligation_definition_id": odef_id, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _row_digest(table: str, key: str, value) -> str:
    """A digest of one stored row, to prove that reads and later decisions leave it unchanged."""
    from app.db import admin_engine
    from app.platform import digest
    with admin_engine.connect() as c:
        row = c.execute(text(f"SELECT * FROM {table} WHERE {key} = :v"), {"v": value}).mappings().one()
    return digest({k: str(v) for k, v in row.items()})


@pytest.fixture(scope="module")
def world(client):
    from app.db import CELL_ID, AdminSession
    from app.models import ControlDefinition, Instrument, LegalEntity, Subject, Tenant

    cd_key = f"CTRL-{TAG}-REGISTER"
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Historical Provenance Instrument", jurisdiction="EU",
                          authority="Regulatory Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=cd_key, title="Maintain contract register", objective="Register is maintained.",
                                 category="RISK_MANAGEMENT"))
        for tid, sid, cco in ORGS.values():
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=f"entity-{tid}", tenant_id=tid, legal_name=f"{tid} Entity Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=f"entity-{tid}", subject_type="LEGAL_ENTITY",
                           display_name=f"{tid} Entity Ltd"))
            add_principal(db, actor_id=cco, tenant_id=tid, actor_type="HUMAN", display_name=cco, roles=["CCO"])
        db.commit()
    S["cd_key"] = cd_key

    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Regulatory Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Historical Provenance Regulation", "locator": f"OJ L {TAG}/hpi", "instrument_identifier": INSTR,
        "raw_text": "Credit institutions shall maintain a digital contract register and assess third-party risk."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    S["req_key"] = f"HPI-{TAG}-REQ"
    S["rv1"] = _requirement(client, S["req_key"], "2025-01-01", "")
    template = {"node_type": "CONTROL", "node_id": f"ctrl_{TAG}", "control_key": cd_key}
    S["odef"] = _definition(f"HPI_{TAG}_MAIN", [p["provision_id"] for p in S["rv1"]["provisions"]], template)

    # R1 and the decision made under it.
    r1 = _release(client, S["odef"], S["rv1"], "r1", "EXECUTABLE_OUTCOME", "CREDIT_INSTITUTION", f"hpi-{TAG}-r1")
    S["R1"], S["rule1"] = r1["content_release"], r1["rule_version"]
    tid, sid, cco = ORGS["A"]
    at = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{sid}/facts", headers=H(cco),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
    S["E1"] = _evaluate(client, cco, sid, S["odef"])
    assert S["E1"]["outcome"] == "APPLIES"
    det_lineage = S["E1"]["current_determination"]["lineage_id"]
    adopt = client.post(f"/api/applicability/determinations/{det_lineage}/attest", headers=H(cco),
                        json={"decision": "ADOPT", "authority_basis": "Board resolution HPI-1"})
    assert adopt.status_code == 200, adopt.text
    S["adopt"] = adopt.json()
    S["lineage"] = S["adopt"]["obligation"]["lineage_id"]
    S["seq_adopted"] = S["adopt"]["obligation"]["record_from_seq"]
    S["E1_row"] = _row_digest("applicability_evaluation", "evaluation_id", S["E1"]["evaluation_id"])

    # Control provenance: a period preview pinned to the expression known now, then expression v2.
    # The obligation is active from E1's effective time; read at cutoffs before its closure.
    e1_at = datetime.fromisoformat(S["E1"]["effective_at"].replace("Z", "+00:00"))
    S["period"] = {"period_from": e1_at.isoformat(), "period_to": (e1_at + timedelta(hours=1)).isoformat()}
    S["seq_expr1"] = client.get("/api/watermark", headers=H(cco)).json()["record_seq"]
    v2 = client.post(f"/api/obligations/{S['lineage']}/expression-versions", headers=H(cco), json={
        "ast": {"node_type": "ALL", "node_id": "v2_all", "children": [
            {"node_type": "CONTROL", "node_id": "v2_c1", "control_key": cd_key, "rationale": "register control"}]},
        "valid_from": "2020-01-01T00:00:00Z", "rationale": "Composite mapping"})
    assert v2.status_code == 201, v2.text
    S["expr2"] = v2.json()

    # R2: a correction release of the rule. Re-evaluation, review, then the human decision that closes the obligation.
    r2 = _release(client, S["odef"], S["rv1"], "c1", "CORRECTION", "PAYMENT_INSTITUTION", f"hpi-{TAG}-r2",
                  correction_reason="Applied to credit institutions in error; payment institutions only.")
    S["R2"], S["rule2"] = r2["content_release"], r2["rule_version"]
    S["E2"] = _evaluate(client, cco, sid, S["odef"])
    assert S["E2"]["outcome"] == "DOES_NOT_APPLY" and S["E2"]["current_determination"]["needs_review"] is True
    close = client.post(f"/api/applicability/determinations/{det_lineage}/attest", headers=H(cco),
                        json={"decision": "ADOPT", "authority_basis": "Board review of correction HPI-2"})
    assert close.status_code == 200, close.text
    S["close"] = close.json()

    # A later correction of the requirement text itself: version 2, released (R3) for another definition, so the
    # version R1 bound becomes SUPERSEDED.
    S["rv2"] = _requirement(client, S["req_key"], "2026-01-01", " (corrected)")
    S["odef2"] = _definition(f"HPI_{TAG}_V2", [p["provision_id"] for p in S["rv2"]["provisions"]], template)
    r3 = _release(client, S["odef2"], S["rv2"], "r3", "EXECUTABLE_OUTCOME", "CREDIT_INSTITUTION", f"hpi-{TAG}-r3")
    S["R3"] = r3["content_release"]
    return True


# ------------------------------------------------------------------------------------------ historical applicability
def test_intact_history_traces_and_replays(client, world):
    """E1 still proves itself after R2 and R3: exact release, rule, requirement content, source, and a full replay."""
    cco = ORGS["A"][2]
    rep = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}", headers=H(cco)).json()
    prov = rep["regulatory_provenance"]
    assert prov["rule_version_id"] == S["rule1"]["rule_version_id"] and prov["binding_basis"] == "RELEASE_BOUND"
    assert prov["integrity"]["status"] == "INTACT", prov["integrity"]
    assert prov["integrity"]["signature_verified"] is False  # trust anchor undecided (K1): never claimed
    req = prov["requirements"][0]
    assert req["requirement"]["requirement_version_id"] == S["rv1"]["requirement_version_id"]
    assert req["content_matches_binding"] is True and req["fingerprint_matches_binding"] is True
    assert req["missing_provision_ids"] == []
    assert rep["replay"]["status"] == "REPRODUCED", rep["replay"]
    assert {c["check"] for c in rep["replay"]["checks"]} == {"RULE_VERSION_PRESENT", "RUNTIME_SUPPORTS_VERSIONS",
                                                             "RUNTIME_WITHIN_LIMITS", "BUNDLE_BINDS_RULE", "STORED_BUNDLE_HASH",
                                                             "EVALUATOR_BUILD", "REDERIVED_BUNDLE", "OUTCOME", "TRACE"}
    assert rep["replay"]["matches"] is True and rep["outcome"] == "APPLIES"
    assert rep["record_watermark"]["record_seq"] >= S["close"]["determination"]["record_from_seq"]


def test_requirement_correction_does_not_rewrite_earlier_evaluation(client, world):
    """The requirement version R1 bound is SUPERSEDED by the corrected version, yet E1 still resolves to it, its bound
    content still hashes, and E1's stored record is unchanged (APP-REQ-012, RUL-REQ-026, TIM-REQ-007)."""
    cco = ORGS["A"][2]
    prov = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}", headers=H(cco)).json()["regulatory_provenance"]
    req = prov["requirements"][0]["requirement"]
    assert req["requirement_version_id"] == S["rv1"]["requirement_version_id"]
    assert req["lifecycle"] == "SUPERSEDED" and req["version_number"] == 1
    assert prov["requirements"][0]["content_matches_binding"] is True
    assert prov["integrity"]["status"] == "INTACT"
    assert _row_digest("applicability_evaluation", "evaluation_id", S["E1"]["evaluation_id"]) == S["E1_row"]


def test_new_rule_version_and_correction_release_keep_both_chains_explainable(client, world):
    """R2 corrects R1: E1 stays on rule 1 / R1, E2 is on rule 2 / R2, and each chain names the other release."""
    cco, sid = ORGS["A"][2], ORGS["A"][1]
    e1 = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}", headers=H(cco)).json()
    e2 = client.get(f"/api/applicability/evaluations/{S['E2']['evaluation_id']}", headers=H(cco)).json()
    r1, r2 = S["R1"]["content_release_id"], S["R2"]["content_release_id"]
    assert e1["regulatory_provenance"]["content_release"]["corrected_by_release_ids"] == [r2]
    assert e2["regulatory_provenance"]["content_release"]["correction_of_release_id"] == r1
    assert e2["regulatory_provenance"]["rule_version_id"] == S["rule2"]["rule_version_id"]
    assert e2["regulatory_provenance"]["integrity"]["status"] == "INTACT" and e2["replay"]["status"] == "REPRODUCED"
    # The same request at E1's record cutoff resolves rule 1, not the newer correction (TIM-REQ-006).
    again = _evaluate(client, cco, sid, S["odef"], record_cutoff_seq=S["E1"]["record_cutoff_seq"],
                      effective_at=S["E1"]["effective_at"])
    assert again["evaluation_id"] == S["E1"]["evaluation_id"] and again["rule_version_id"] == S["rule1"]["rule_version_id"]


def test_requirement_content_substitution_is_detected(client, world):
    """A released requirement version edited in place no longer matches the digest its release bound."""
    cco = ORGS["A"][2]
    with _tampered("catalog_requirement_version", "requirement_version_id", S["rv1"]["requirement_version_id"],
                   title="Silently reworded requirement"):
        prov = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}",
                          headers=H(cco)).json()["regulatory_provenance"]
        assert prov["requirements"][0]["content_matches_binding"] is False
        assert prov["integrity"]["status"] == "MISMATCH"
        assert "REQUIREMENT_CONTENT_MISMATCH" in {p["code"] for p in prov["integrity"]["problems"]}
        dossier = client.get(f"/api/obligations/{S['lineage']}", params={"as_of_record_seq": S["seq_adopted"]},
                             headers=H(cco)).json()
        assert dossier["regulatory_provenance"]["integrity"]["status"] == "MISMATCH"


def test_source_fingerprint_substitution_is_detected(client, world):
    cco = ORGS["A"][2]
    with _tampered("catalog_source_record", "source_id", S["source"]["source_id"], fingerprint="e" * 64):
        prov = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}",
                          headers=H(cco)).json()["regulatory_provenance"]
        assert {p["code"] for p in prov["integrity"]["problems"]} >= {"SOURCE_FINGERPRINT_MISMATCH"}
        assert prov["integrity"]["status"] == "MISMATCH"


def test_missing_bound_provision_is_reported_incomplete(client, world, monkeypatch):
    """A provision the release bound that can no longer be found makes the chain INCOMPLETE; it is not dropped."""
    from app.db import AdminSession
    from app.engines import catalog
    from app.models import RuleVersion
    real = catalog.release_bound_requirements

    def with_ghost(db, rule):
        bound = real(db, rule)
        return [{**bound[0], "provision_ids": [*bound[0]["provision_ids"], "provision-that-is-gone"]}]
    monkeypatch.setattr(catalog, "release_bound_requirements", with_ghost)
    with AdminSession() as db:
        trace = catalog.trace_rule_version(db, db.get(RuleVersion, S["rule1"]["rule_version_id"]))
    assert trace["requirements"][0]["missing_provision_ids"] == ["provision-that-is-gone"]
    assert trace["integrity"]["status"] == "INCOMPLETE"
    assert trace["requirements"][0]["content_matches_binding"] is None  # not provable without every bound provision


def test_substituted_rule_version_fails_replay_closed_but_dossier_reports_it(client, world):
    """A rule AST changed in place: the evaluation read refuses (no fallback, APP-REQ-006/019); the dossier, which does
    not evaluate, reports the same failure as a MISMATCH instead of showing the chain as intact."""
    cco = ORGS["A"][2]
    with _tampered("catalog_rule_version", "rule_version_id", S["rule1"]["rule_version_id"], ast_hash="0" * 64):
        r = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}", headers=H(cco))
        assert r.status_code == 500 and r.json()["detail"]["error"] == "RUL_SIGNATURE_INVALID"
        d = client.get(f"/api/obligations/{S['lineage']}", params={"as_of_record_seq": S["seq_adopted"]}, headers=H(cco))
        assert d.status_code == 200
        problems = d.json()["regulatory_provenance"]["integrity"]["problems"]
        assert any(p["code"] == "RULE_INTEGRITY_FAILED" and p["check"] == "AST_HASH_MISMATCH" for p in problems)


def test_missing_rule_version_is_incomplete_not_substituted(client, world):
    """An evaluation naming a RuleVersion that cannot be found is shown as missing; nothing resolves another version."""
    from app.db import AdminSession
    from app.models import Actor, ApplicabilityEvaluation
    from app.routers.applicability import _eval_view, _missing_rule_provenance, _replay
    from app.security import Ctx
    cco = ORGS["A"][2]
    with AdminSession() as db:
        src = db.get(ApplicabilityEvaluation, S["E1"]["evaluation_id"])
        ghost = ApplicabilityEvaluation(
            tenant_id=src.tenant_id, entity_id=src.entity_id, subject_id=src.subject_id,
            obligation_definition_id=src.obligation_definition_id, rule_version_id="rule-version-that-is-gone",
            effective_at=src.effective_at, record_cutoff_seq=src.record_cutoff_seq, evaluator_build=src.evaluator_build,
            input_bundle=src.input_bundle, input_bundle_hash=src.input_bundle_hash, evaluation_key=f"ghost-{TAG}",
            outcome=src.outcome, trace=src.trace, trigger="RECONSTRUCTION", requested_by=cco, record_seq=src.record_seq)
        db.add(ghost)
        db.flush()
        view = _eval_view(db, ghost)
        assert view["rule_version"] == {"rule_version_id": "rule-version-that-is-gone", "missing": True}
        assert view["regulatory_provenance"] is None
        assert _missing_rule_provenance(ghost)["integrity"]["status"] == "INCOMPLETE"
        replay = _replay(Ctx(db=db, actor=db.get(Actor, cco)), ghost)
        assert replay["status"] == "INCOMPLETE" and replay["matches"] is False
        db.rollback()


def test_stored_bundle_and_trace_substitution_are_detected_by_replay(client, world):
    cco = ORGS["A"][2]
    url = f"/api/applicability/evaluations/{S['E1']['evaluation_id']}"
    with _tampered("applicability_evaluation", "evaluation_id", S["E1"]["evaluation_id"],
                   input_bundle=lambda row: {**row["input_bundle"], "record_cutoff_seq": 1}):
        replay = client.get(url, headers=H(cco)).json()["replay"]
        assert replay["status"] == "NOT_REPRODUCED" and "STORED_BUNDLE_HASH" in replay["failed_checks"]
    with _tampered("applicability_evaluation", "evaluation_id", S["E1"]["evaluation_id"],
                   trace=lambda row: {**row["trace"], "result": "FALSE"}):
        replay = client.get(url, headers=H(cco)).json()["replay"]
        assert replay["failed_checks"] == ["TRACE"] and replay["status"] == "NOT_REPRODUCED"
    assert client.get(url, headers=H(cco)).json()["replay"]["status"] == "REPRODUCED"


# ------------------------------------------------------------------------------------------ obligation lifecycle
def test_adoption_and_closure_are_each_explained_by_their_own_decision(client, world):
    cco = ORGS["A"][2]
    hist = client.get(f"/api/obligations/{S['lineage']}", headers=H(cco)).json()["decision_history"]
    assert hist["integrity"]["status"] == "INTACT", hist["integrity"]
    adopted, closed = hist["revisions"]
    assert adopted["lifecycle"] == "ADOPTED" and closed["lifecycle"] == "CLOSED"
    # Origin: the R1 adoption (determination -> attestation -> E1 under rule 1)
    o = adopted["origin"]
    assert o["determination"]["revision_id"] == S["adopt"]["determination"]["revision_id"]
    assert o["attestation"]["authority_basis"] == "Board resolution HPI-1" and o["attestation"]["decision"] == "ATTESTED"
    assert o["evaluation"]["evaluation_id"] == S["E1"]["evaluation_id"]
    assert o["evaluation"]["content_release_id"] == S["R1"]["content_release_id"]
    # The closed revision keeps its origin and is explained by the R2 decision recorded at its sequence.
    assert closed["origin"]["determination"]["revision_id"] == o["determination"]["revision_id"]
    c = closed["closure"]
    assert c["determination"]["revision_id"] == S["close"]["determination"]["revision_id"]
    assert c["determination"]["outcome"] == "DOES_NOT_APPLY"
    assert c["attestation"]["attestation_id"] == S["close"]["attestation"]["attestation_id"]
    assert c["attestation"]["authority_basis"] == "Board review of correction HPI-2"
    assert c["evaluation"]["evaluation_id"] == S["E2"]["evaluation_id"]
    assert c["evaluation"]["content_release_id"] == S["R2"]["content_release_id"]


def test_history_at_adoption_cutoff_shows_only_what_was_known(client, world):
    cco = ORGS["A"][2]
    d = client.get(f"/api/obligations/{S['lineage']}", params={"as_of_record_seq": S["seq_adopted"]}, headers=H(cco)).json()
    revs = d["decision_history"]["revisions"]
    assert [r["lifecycle"] for r in revs] == ["ADOPTED"]
    assert revs[0]["record_to_seq"] is None  # its closure was recorded later and was not known at the cutoff
    assert d["record_watermark"] == {"cell_id": d["record_watermark"]["cell_id"], "record_seq": S["seq_adopted"],
                                     "perspective": "AS_OF_RECORD_SEQ"}
    assert d["historical_limitations"]
    assert [e["version"] for e in d["expression_versions"]] == [1]


def test_superseded_determinations_and_old_evaluation_are_retained(client, world):
    cco = ORGS["A"][2]
    hist = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}",
                      headers=H(cco)).json()["determination_history"]
    states = [(h["state"], h["outcome"], h["needs_review"]) for h in hist]
    assert states[0] == ("PROPOSED", "APPLIES", False)
    assert ("ADOPTED", "APPLIES", False) in states and ("ADOPTED", "APPLIES", True) in states
    assert states[-1] == ("ADOPTED", "DOES_NOT_APPLY", False)
    assert all(h["record_to_seq"] is not None for h in hist[:-1]) and hist[-1]["record_to_seq"] is None


def test_obligation_with_missing_attestation_is_incomplete(client, world):
    cco = ORGS["A"][2]
    with _tampered("assurance_obligation_instance_revision", "revision_id", S["adopt"]["obligation"]["revision_id"],
                   attestation_id="attestation-that-is-gone"):
        hist = client.get(f"/api/obligations/{S['lineage']}", headers=H(cco)).json()["decision_history"]
    assert hist["integrity"]["status"] == "INCOMPLETE"
    assert any(p["code"] == "ATTESTATION_MISSING" and p["revision_id"] == S["adopt"]["obligation"]["revision_id"]
               for p in hist["integrity"]["problems"])


def test_determination_not_matching_its_evaluation_is_a_mismatch(client, world):
    cco = ORGS["A"][2]
    with _tampered("applicability_determination_revision", "revision_id", S["adopt"]["determination"]["revision_id"],
                   outcome="DOES_NOT_APPLY"):
        d = client.get(f"/api/obligations/{S['lineage']}", params={"as_of_record_seq": S["seq_adopted"]},
                       headers=H(cco)).json()
    codes = {p["code"] for p in d["decision_history"]["integrity"]["problems"]}
    assert {"EVALUATION_MISMATCH", "OUTCOME_DOES_NOT_SUPPORT_DECISION"} <= codes
    assert d["regulatory_provenance"]["integrity"]["status"] == "MISMATCH"


# ------------------------------------------------------------------------------------------ control provenance
def test_historical_cutoff_selects_the_expression_version_known_then(client, world):
    cco = ORGS["A"][2]
    url = f"/api/obligations/{S['lineage']}/preview"
    old = client.post(url, headers=H(cco), json={**S["period"], "record_cutoff_seq": S["seq_expr1"]})
    new = client.post(url, headers=H(cco), json={**S["period"], "record_cutoff_seq": S["close"]["determination"]["record_from_seq"] - 1})
    assert old.status_code == 200 and new.status_code == 200, (old.text, new.text)
    assert {s["expression_version_id"] for s in old.json()["segments"]} != {S["expr2"]["expression_version_id"]}
    assert {s["expression_version_id"] for s in new.json()["segments"]} == {S["expr2"]["expression_version_id"]}


def test_substituted_expression_version_is_detected_and_fails_closed(client, world):
    cco = ORGS["A"][2]
    vid = S["expr2"]["expression_version_id"]
    with _tampered("assurance_satisfaction_expression_version", "expression_version_id", vid,
                   ast={"node_type": "CONTROL", "node_id": "swapped", "control_key": "SOME_OTHER_CONTROL"}):
        vs = client.get(f"/api/obligations/{S['lineage']}/expression-versions", headers=H(cco)).json()
        assert {v["expression_version_id"]: v["integrity"]["ast_hash_matches"] for v in vs}[vid] is False
        r = client.post(f"/api/obligations/{S['lineage']}/preview", headers=H(cco),
                        json={**S["period"], "record_cutoff_seq": S["close"]["determination"]["record_from_seq"] - 1})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "SATISFACTION_EXPRESSION_HASH_MISMATCH"
    vs = client.get(f"/api/obligations/{S['lineage']}/expression-versions", headers=H(cco)).json()
    assert all(v["integrity"]["ast_hash_matches"] for v in vs)


def test_snapshot_expression_pins_are_checked(client, world):
    cco = ORGS["A"][2]
    snap = client.post(f"/api/obligations/{S['lineage']}/snapshots", headers=H(cco),
                       json={**S["period"], "record_cutoff_seq": S["close"]["determination"]["record_from_seq"] - 1})
    assert snap.status_code == 200, snap.text
    sid = snap.json()["snapshot_id"]

    def pins():
        d = client.get(f"/api/obligations/{S['lineage']}", headers=H(cco)).json()
        return next(s for s in d["snapshots"] if s["snapshot_id"] == sid)["expression_pins"]
    # The manifest pins every version known at the cutoff; the segments name the one in force (v2).
    assert pins()["status"] == "INTACT" and S["expr2"]["expression_version_id"] in pins()["pinned"]
    with _tampered("assurance_satisfaction_expression_version", "expression_version_id", S["expr2"]["expression_version_id"],
                   ast={"node_type": "CONTROL", "node_id": "swapped", "control_key": "SOME_OTHER_CONTROL"}):
        assert pins()["status"] == "MISMATCH"
    with _tampered("assurance_snapshot", "snapshot_id", sid,
                   dependency_manifest=lambda row: {**row["dependency_manifest"], "expression_versions": ["expression-that-is-gone"]},
                   segments=lambda row: [{**s, "expression_version_id": "expression-that-is-gone"} for s in row["segments"]]):
        p = pins()
        assert p["status"] == "INCOMPLETE" and p["missing"] == ["expression-that-is-gone"]


# ------------------------------------------------------------------------------------------ tenancy and temporal reads
def test_other_organisation_cannot_read_any_of_the_history(client, world):
    cco_b = ORGS["B"][2]
    assert client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}", headers=H(cco_b)).status_code == 404
    assert client.get(f"/api/obligations/{S['lineage']}", params={"as_of_record_seq": S["seq_adopted"]},
                      headers=H(cco_b)).status_code == 404
    assert client.get(f"/api/obligations/{S['lineage']}/expression-versions", headers=H(cco_b)).status_code == 404


def test_historical_reads_are_deterministic_and_write_nothing(client, world):
    from app.db import admin_engine
    cco = ORGS["A"][2]
    tables = ["applicability_evaluation", "applicability_determination_revision", "assurance_obligation_instance_revision",
              "assurance_satisfaction_expression_version", "assurance_snapshot", "governance_attestation",
              "platform_audit_event", "platform_outbox_event"]

    def counts():
        with admin_engine.connect() as c:
            return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}

    def reads():
        e = client.get(f"/api/applicability/evaluations/{S['E1']['evaluation_id']}", headers=H(cco)).json()
        d = client.get(f"/api/obligations/{S['lineage']}", params={"as_of_record_seq": S["seq_adopted"]},
                       headers=H(cco)).json()
        return ({k: e[k] for k in ("replay", "regulatory_provenance", "determination_history", "outcome")},
                {k: d[k] for k in ("decision_history", "regulatory_provenance", "obligation", "expression_versions")})
    before = counts()
    first = reads()
    assert reads() == first
    assert counts() == before
