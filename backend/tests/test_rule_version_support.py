"""RUL-REQ-028: "Unsupported schema/operator versions MUST be rejected explicitly and MUST NOT be interpreted
approximately by older runtimes."

A RuleVersion declaring a RuleSchemaVersion or OperatorSetVersion the evaluator does not implement is refused with the
ZA-APP-001 §12 error SCHEMA_INCOMPATIBLE ("Rule references unsupported/retired FactDefinition or operator version"):
  * an error, never an applicability outcome or UNCERTAIN (APP-REQ-019), and nothing is persisted;
  * no fallback to an older, supported RuleVersion (the resolved version is the binding one, APP-REQ-006);
  * historical evaluations stay readable; a replay of an unsupported version reports that it cannot run, it does not run
    approximately;
  * a correctly signed release targeting a newer runtime is not an integrity failure (no RUL_SIGNATURE_INVALID signal).

The unsupported version is published the way the release path publishes (signed manifest declaring its versions, AST
hash computed under them); only the declared versions differ, because the API always stamps the runtime's own.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RVS-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TEN, CCO = f"tenant-rvs-{TAG.lower()}", f"rvs-cco-{TAG.lower()}"
S: dict = {}
_n = iter(range(10**6))


def _ast(tag: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{tag}_{nid}", "operator": "EQ", "source_mappings": [f"sm_{tag}_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{tag}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{tag}_{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred("a", "entity.established_in_eu", "BOOLEAN", True),
        pred("b", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}


VECTORS = [
    {"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "APPLIES"},
    {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"}]


def _definition() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RVS_{TAG}_{next(_n)}", title="Version support obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _publish_supported(client, odef_id: str) -> dict:
    """A RuleVersion published through the API (the runtime's own versions)."""
    tag = f"v{next(_n)}"
    p = S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{tag}_{m}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for m in ("root", "a", "b")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"rvs-{did[:8]}", "changelog": "probe"})
    assert r.status_code == 200, r.text
    return r.json()["rule_version"]


def _publish_declaring(base: dict, *, schema: str, operators: str) -> str:
    """The next RuleVersion of `base`'s definition, released exactly as the release path releases one (canonical AST,
    AST hash under the declared versions, signed manifest entry declaring them), but declaring other versions."""
    from app.db import AdminSession
    from app.engines import keys, rules
    from app.models import ContentRelease, RuleVersion, new_id
    from app.platform import canonical_json, next_record_seq, sha256_hex
    with AdminSession() as db:
        prev = db.get(RuleVersion, base["rule_version_id"])
        top = db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == prev.obligation_definition_id)
                         .order_by(RuleVersion.version.desc())).scalars().first()
        seq = next_record_seq(db)
        rv_id, cr_id = new_id(), new_id()
        h = rules.ast_hash(prev.canonical_ast, {}, rule_schema_version=schema, operator_set_version=operators)
        db.add(RuleVersion(rule_version_id=rv_id, obligation_definition_id=prev.obligation_definition_id, version=top.version + 1,
                           valid_from=prev.valid_from, valid_to=None, coverage_mode="ENCODED", rule_schema_version=schema,
                           operator_set_version=operators, canonical_ast=prev.canonical_ast, ast_hash=h,
                           required_facts=prev.required_facts, set_refs=[], source_mappings=prev.source_mappings,
                           golden_vectors=prev.golden_vectors, status="PUBLISHED", content_release_id=cr_id, record_seq=seq))
        db.flush()
        manifest = json.loads(canonical_json({"label": f"rvs-{schema}-{operators}", "set_refs": [], "rule_versions": [
            {"rule_version_id": rv_id, "ast_sha256": h, "rule_schema_version": schema, "operator_set_version": operators,
             "valid_from": prev.valid_from.isoformat(), "valid_to": None}]}))
        mb = canonical_json(manifest)
        db.add(ContentRelease(content_release_id=cr_id, label=f"rvs-{schema}-{operators}"[:64], manifest=manifest,
                              manifest_hash=sha256_hex(mb), signature=keys.sign(mb), signer_key_id=keys.signer_key_id(),
                              changelog="probe", released_by=RELEASE, cell_received_seq=seq))
        db.commit()
        return rv_id


def _subject(client, name: str) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TEN, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TEN, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
    return sid


def _evaluate(client, sid, odef_id, **extra):
    return client.post("/api/applicability/evaluations", headers=H(CCO),
                       json={"subject_id": sid, "obligation_definition_id": odef_id, **extra})


def _evaluations(sid, odef_id):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.subject_id == sid,
                                                                ApplicabilityEvaluation.obligation_definition_id == odef_id)
                          .order_by(ApplicabilityEvaluation.record_seq)).scalars().all()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Version Support Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TEN, legal_name=f"{TEN} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TEN, actor_type="HUMAN", display_name="RVS CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Version Support Act",
        "locator": f"OJ L {TAG}/rvs", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RVS-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 3", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 3(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return True


# ------------------------------------------------------------------------------------------------ supported
def test_runtime_declares_what_it_supports():
    from app.engines import rules
    assert rules.SUPPORTED_SCHEMA_VERSIONS == {rules.SCHEMA_VERSION}
    assert rules.SUPPORTED_OPERATOR_SET_VERSIONS == {rules.OPERATOR_SET_VERSION}
    assert rules.unsupported_versions(rules.SCHEMA_VERSION, rules.OPERATOR_SET_VERSION) is None
    assert rules.unsupported_versions("2.0", None) == {"rule_schema_version": "2.0", "operator_set_version": None}


def test_supported_versions_evaluate_and_replay_normally(client, world):
    odef = _definition()
    v1 = _publish_supported(client, odef)
    assert (v1["rule_schema_version"], v1["operator_set_version"]) == ("1.0", "1.0")
    sid = _subject(client, "ok")
    r = _evaluate(client, sid, odef)
    assert r.status_code == 200 and r.json()["outcome"] == "APPLIES", r.text
    replay = client.get(f"/api/applicability/evaluations/{r.json()['evaluation_id']}", headers=H(CCO)).json()["replay"]
    assert replay["status"] == "REPRODUCED"
    assert {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": True} in replay["checks"]


# ------------------------------------------------------------------------------------------------ refused
@pytest.mark.parametrize("schema, operators, unsupported", [
    ("2.0", "1.0", {"rule_schema_version": "2.0"}),
    ("1.0", "2.0", {"operator_set_version": "2.0"}),
    ("2.0", "2.0", {"rule_schema_version": "2.0", "operator_set_version": "2.0"}),
    ("1.1", "1.0", {"rule_schema_version": "1.1"}),
])
def test_unsupported_version_is_refused_explicitly_without_fallback_or_writes(client, world, schema, operators, unsupported):
    odef = _definition()
    v1 = _publish_supported(client, odef)
    v2 = _publish_declaring(v1, schema=schema, operators=operators)
    sid = _subject(client, f"bad{next(_n)}")
    before = db_fingerprint()
    r = _evaluate(client, sid, odef)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "SCHEMA_INCOMPATIBLE" and detail["rule_version_id"] == v2
    assert detail["unsupported"] == unsupported
    assert detail["supported"] == {"rule_schema_version": ["1.0"], "operator_set_version": ["1.0"]}
    # Not an outcome, not UNCERTAIN, not a fallback to the supported v1, and not an integrity signal: nothing written.
    assert db_fingerprint() == before
    assert _evaluations(sid, odef) == []


def test_refusal_is_not_an_integrity_failure(client, world):
    from app.db import admin_engine
    odef = _definition()
    v2 = _publish_declaring(_publish_supported(client, odef), schema="2.0", operators="1.0")
    sid = _subject(client, "noint")
    assert _evaluate(client, sid, odef).status_code == 409
    with admin_engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM platform_outbox_event WHERE event_type = 'security.content_signature_failed' "
                           "AND payload::text LIKE :p" if admin_engine.dialect.name == "postgresql" else
                           "SELECT count(*) FROM platform_outbox_event WHERE event_type = 'security.content_signature_failed' "
                           "AND payload LIKE :p"), {"p": f"%{v2}%"}).scalar()
    assert n == 0


def test_dependent_paths_report_the_refusal_instead_of_evaluating(client, world):
    """Fact-change re-evaluation and evaluate-all name the refused version per definition; the fact is still recorded."""
    odef = _definition()
    v1 = _publish_supported(client, odef)
    sid = _subject(client, "dep")
    first = _evaluate(client, sid, odef)
    assert first.status_code == 200
    _publish_declaring(v1, schema="2.0", operators="1.0")
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": "entity.established_in_eu", "value": False, "change_kind": "PROSPECTIVE_CHANGE", "reason": "moved",
        "valid_from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    mine = [x for x in r.json()["impact_reevaluations"] if x.get("error") and x["error"].get("rule_version_id")]
    assert mine and all(x["error"]["error"] == "SCHEMA_INCOMPATIBLE" for x in mine)
    allr = client.post("/api/applicability/evaluate-all", headers=H(CCO), params={"subject_id": sid})
    assert allr.status_code == 200
    assert any(isinstance(x.get("error"), dict) and x["error"].get("error") == "SCHEMA_INCOMPATIBLE" for x in allr.json())
    assert [e.evaluation_id for e in _evaluations(sid, odef)] == [first.json()["evaluation_id"]]  # nothing new


def test_history_stays_readable_and_replay_does_not_run_an_unsupported_version(client, world):
    from app.db import AdminSession
    from app.models import Actor, ApplicabilityEvaluation
    from app.routers.applicability import _replay
    from app.security import Ctx
    odef = _definition()
    v1 = _publish_supported(client, odef)
    sid = _subject(client, "hist")
    e1 = _evaluate(client, sid, odef).json()
    v2 = _publish_declaring(v1, schema="2.0", operators="1.0")
    # The earlier evaluation still reads and reproduces under its own (supported) version.
    rep = client.get(f"/api/applicability/evaluations/{e1['evaluation_id']}", headers=H(CCO))
    assert rep.status_code == 200 and rep.json()["replay"]["status"] == "REPRODUCED"
    # Asked again at its own record cutoff, the request resolves v1 (v2 was not yet known) and collapses to it.
    again = _evaluate(client, sid, odef, record_cutoff_seq=e1["record_cutoff_seq"], effective_at=e1["effective_at"])
    assert again.status_code == 200 and again.json()["evaluation_id"] == e1["evaluation_id"]
    # An evaluation recorded under the unsupported version (another runtime) is reported as not reproducible here.
    with AdminSession() as db:
        src = db.get(ApplicabilityEvaluation, e1["evaluation_id"])
        other = ApplicabilityEvaluation(
            tenant_id=src.tenant_id, entity_id=src.entity_id, subject_id=src.subject_id,
            obligation_definition_id=src.obligation_definition_id, rule_version_id=v2, effective_at=src.effective_at,
            record_cutoff_seq=src.record_cutoff_seq, evaluator_build=src.evaluator_build, input_bundle=src.input_bundle,
            input_bundle_hash=src.input_bundle_hash, evaluation_key=f"rvs-{TAG}", outcome=src.outcome, trace=src.trace,
            trigger="RECONSTRUCTION", requested_by=CCO, record_seq=src.record_seq)
        out = _replay(Ctx(db=db, actor=db.get(Actor, CCO)), other)
        db.rollback()
    assert out["status"] == "NOT_REPRODUCED" and out["error"] == "SCHEMA_INCOMPATIBLE" and out["matches"] is False
    assert out["failed_checks"] == ["RUNTIME_SUPPORTS_VERSIONS"] and out["unsupported"] == {"rule_schema_version": "2.0"}


def test_a_supported_successor_restores_evaluation(client, world):
    odef = _definition()
    v1 = _publish_supported(client, odef)
    _publish_declaring(v1, schema="2.0", operators="1.0")
    sid = _subject(client, "recover")
    assert _evaluate(client, sid, odef).status_code == 409
    v3 = _publish_supported(client, odef)
    r = _evaluate(client, sid, odef)
    assert r.status_code == 200 and r.json()["rule_version_id"] == v3["rule_version_id"] and v3["version"] == 3
