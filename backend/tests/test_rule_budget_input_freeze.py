"""RUL-REQ-029 on the paths that commit despite a refused evaluation: nothing from the refused rule is persisted.

ZA-RUL-001 RUL-REQ-029: "Rule complexity limits MUST be enforced at validation and runtime; budget failures are
infrastructure errors, not UNCERTAIN." §11: "Budget breach is infrastructure error, never UNCERTAIN." ZA-APP-001 §12
EVALUATOR_UNAVAILABLE: "Infrastructure error before deterministic result".

A single evaluation that is refused rolls its whole request back. Fact-change re-evaluation and the publication impact
loop do not: they record the refusal for one definition and commit their own work (the fact, the release). Input freeze
can write (resolve_fact records a FactConflict when admissible assertions disagree), so on those paths "nothing is
persisted for the refused rule" depends on the limits being checked before input freeze (appl.require_within_limits),
not only by the evaluator's own guard after it. These tests prove it with a genuine conflict: refused -> no conflict row;
the same evaluation within the limits -> the conflict row is written (the fixture is not vacuous).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"RBF-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TEN, CCO = f"tenant-rbf-{TAG.lower()}", f"rbf-cco-{TAG.lower()}"
S: dict = {}
_n = iter(range(10**6))
VECTORS = [
    {"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "APPLIES"},
    {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"}]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Budget Input Freeze Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TEN, legal_name=f"{TEN} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TEN, actor_type="HUMAN", display_name="RBF CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Budget Act",
        "locator": f"OJ L {TAG}/rbf", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RBF-{TAG}-REQ",
        "title": "Register", "locator": "Art. 3", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 3(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return True


def _definition() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RBF_{TAG}_{next(_n)}", title="Budget obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _publish(client, odef_id, *, extra: int, valid_from="2025-01-01") -> dict:
    """ALL(established_in_eu, financial_entity_type, `extra` more established_in_eu predicates), mapped at the root."""
    tag = f"v{next(_n)}"

    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{tag}_{nid}", "operator": "EQ",
                "args": [{"node_type": "FACT_REF", "node_id": f"{tag}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{tag}_{nid}c", "type": typ, "value": value}]}
    kids = [pred("a", "entity.established_in_eu", "BOOLEAN", True),
            pred("b", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]
    kids += [pred(f"x{i}", "entity.established_in_eu", "BOOLEAN", True) for i in range(extra)]
    p = S["rv"]["provisions"][0]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "golden_vectors": VECTORS, "valid_from": valid_from,
        "change_class": "EXECUTABLE_OUTCOME",
        "ast": {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}"], "children": kids},
        "source_mappings": [{"source_mapping_id": f"sm_{tag}", "requirement_version_id": S["rv"]["requirement_version_id"],
                             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT"}]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"rbf-{did[:8]}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return r.json()


def _nodes(rv_id) -> int:
    from app.db import AdminSession
    from app.engines import rules
    from app.models import RuleVersion
    with AdminSession() as db:
        ast = db.get(RuleVersion, rv_id).canonical_ast
    saved = dict(rules.LIMITS)
    try:
        rules.LIMITS["max_nodes"] = 0
        return next(v["actual"] for v in rules.resource_limit_violations(ast) if v["limit"] == "max_nodes")
    finally:
        rules.LIMITS.update(saved)


def _subject(client) -> str:
    """A legal entity with established_in_eu = True and financial_entity_type = CREDIT_INSTITUTION, plus a second,
    disagreeing financial_entity_type assertion recorded below the API (as a concurrent import could): resolving that
    fact at input freeze records a FactConflict."""
    from app.db import CELL_ID, AdminSession
    from app.engines.facts import typed_columns
    from app.models import FactAssertion, FactDefinition, LegalEntity, Subject, new_id
    from app.platform import next_record_seq
    sid = f"s-rbf{next(_n)}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TEN, legal_name=f"{sid} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TEN, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY", display_name=sid))
        db.commit()
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == "entity.financial_entity_type")).scalars().one()
        cur = db.execute(select(FactAssertion).where(FactAssertion.subject_id == sid, FactAssertion.fact_definition_id
                                                     == fd.fact_definition_id, FactAssertion.record_to_seq.is_(None))).scalars().one()
        row = {c.name: getattr(cur, c.name) for c in FactAssertion.__table__.columns}
        row.update(typed_columns(fd, "PAYMENT_INSTITUTION"), revision_id=new_id(), lineage_id=new_id(), record_batch_id=new_id(),
                   record_from_seq=next_record_seq(db), source_ref="concurrent-import")
        db.add(FactAssertion(**row))
        db.commit()
        S.setdefault("fd", fd.fact_definition_id)
    return sid


def _conflicts(sid) -> int:
    from app.db import AdminSession
    from app.models import FactConflict
    with AdminSession() as db:
        return len(db.execute(select(FactConflict).where(FactConflict.subject_id == sid,
                                                         FactConflict.fact_definition_id == S["fd"])).scalars().all())


def _evaluations(sid, odef) -> list:
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return [e.evaluation_id for e in db.execute(select(ApplicabilityEvaluation).where(
            ApplicabilityEvaluation.subject_id == sid, ApplicabilityEvaluation.obligation_definition_id == odef)).scalars()]


def _evaluate(client, sid, odef):
    return client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": odef})


def _prior_evaluation(client, sid, odef, monkeypatch):
    """An earlier evaluation, so the dependent paths re-evaluate this definition for the subject. Taken with conflict
    recording off, so the conflict row is created (or not) only by the path under test."""
    from app.engines import applicability as appl
    real = appl.resolve_fact
    with monkeypatch.context() as m:
        m.setattr(appl, "resolve_fact", lambda *a, **k: real(*a, **{**k, "create_conflicts": False}))
        r = _evaluate(client, sid, odef)
    assert r.status_code == 200 and r.json()["outcome"] == "UNCERTAIN", r.text  # the conflict makes the fact unknown
    assert _conflicts(sid) == 0
    return r.json()["evaluation_id"]


def _control_records_the_conflict(client, sid, odef):
    """Within the limits, the same evaluation resolves the same facts and does record the conflict."""
    r = _evaluate(client, sid, odef)
    assert r.status_code == 200 and r.json()["outcome"] == "UNCERTAIN", r.text
    assert _conflicts(sid) == 1


def test_fact_change_reevaluation_persists_nothing_for_a_refused_rule(client, world, monkeypatch):
    from app.engines import rules
    odef = _definition()
    rv_id = _publish(client, odef, extra=2)["rule_version"]["rule_version_id"]
    sid = _subject(client)
    first = _prior_evaluation(client, sid, odef, monkeypatch)
    with monkeypatch.context() as m:
        m.setitem(rules.LIMITS, "max_nodes", _nodes(rv_id) - 1)
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": "entity.established_in_eu", "value": True, "change_kind": "PROSPECTIVE_CHANGE", "reason": "re-stated",
            "valid_from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
        assert r.status_code == 200, r.text  # the fact itself is recorded and committed
        mine = [x["error"] for x in r.json()["impact_reevaluations"]
                if isinstance(x.get("error"), dict) and x["error"].get("rule_version_id") == rv_id]
        assert len(mine) == 1 and mine[0]["error"] == "EVALUATOR_UNAVAILABLE" and mine[0]["reason"] == "RUL_RESOURCE_LIMIT"
        assert _conflicts(sid) == 0 and _evaluations(sid, odef) == [first]
    _control_records_the_conflict(client, sid, odef)


def test_publication_impact_persists_nothing_for_a_refused_rule(client, world, monkeypatch):
    from app.engines import rules
    odef = _definition()
    v1 = _publish(client, odef, extra=2)["rule_version"]["rule_version_id"]
    sid = _subject(client)
    first = _prior_evaluation(client, sid, odef, monkeypatch)
    with monkeypatch.context() as m:
        m.setitem(rules.LIMITS, "max_nodes", _nodes(v1) - 1)  # the version in force is over; the new one is within
        out = _publish(client, odef, extra=0, valid_from="2099-01-01")
        impact = out["impact"]
        assert impact["status"] == "INCOMPLETE" and impact["failed"].get("EVALUATOR_UNAVAILABLE", 0) >= 1, impact
        assert _conflicts(sid) == 0 and _evaluations(sid, odef) == [first]
    _control_records_the_conflict(client, sid, odef)
