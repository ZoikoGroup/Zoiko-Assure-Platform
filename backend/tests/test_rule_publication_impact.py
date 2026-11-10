"""Rule publication and applicability impact integrity.

Controlling text:
  * ZA-RUL-001 §14 (CORRECTION: "affected-tenant impact evaluation and preserved prior record-time history"), §15 gate
    and fail-closed rules ("Shadow-job infrastructure failures are not treated as 'no impact'"; impact review of
    APPLIES/DNP changes, new UNCERTAINs and adopted-determination conflicts), RUL-REQ-008/009/010/020/026/027.
  * ZA-APP-001 §10 ("RuleVersion published/corrected: New evaluation; adopted determination flagged for review if outcome
    materially differs"), §11 C, §15 one current determination, APP-REQ-012/014 (impact output to content operations is
    aggregate, no customer identities), APP-REQ-016.
  * ZA-TIM-001 TIM-REQ-007/012/017, §17 impact index; TIM-REQ-026 (future-effective content does not bind early).
  * ZA-EVT-001: content_release.published, content_release.corrected, rule.superseded.
  * INV-06 / INV-09: binding content rests on verified requirement versions only.

Concurrency tests (PostgreSQL 17) hold one transaction inside its critical section and show that the other waits, so
interleavings are deterministic. The one global lock order they protect: obligation-definition publication lock (shared
by evaluations, exclusive for a publication) -> decision-scope locks (sorted) -> the cell record-sequence row.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint
from test_rule_integrity import _tampered

TAG = secrets.token_hex(3).upper()
INSTR = f"RPI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-rpia-{TAG.lower()}", f"tenant-rpib-{TAG.lower()}"
CCO, CCO_B = f"rpi-cco-{TAG.lower()}", f"rpib-cco-{TAG.lower()}"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


# ------------------------------------------------------------------------------------------------ helpers
def _ast(tag: str, entity_type: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", entity_type)]}


def _vectors(t: str) -> list[dict]:
    facts = lambda eu, v: {"entity.established_in_eu": eu, "entity.financial_entity_type": v}  # noqa: E731
    return [{"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
             "facts": facts(True, t), "expected_outcome": "APPLIES"},
            {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
             "facts": facts(False, t), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
             "facts": facts(True, {"unknown": "MISSING_REQUIRED_FACT"}), "expected_outcome": "UNCERTAIN"}]


def _mapping(sid: str, rv: dict, p: dict, **over) -> dict:
    return {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
            "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
            "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL, **over}


def _draft(client, tag: str, entity_type="CREDIT_INSTITUTION", change_class="EXECUTABLE_OUTCOME",
           valid_from="2025-01-01", mappings=None, rv=None):
    """A reviewed, shadowed draft ready for release. Returns (draft_id, None) or (None, the first refusal)."""
    rv = rv or S["rv"]
    p = rv["provisions"][0]
    maps = mappings if mappings is not None else [_mapping(f"sm_{tag}_{n}", rv, p) for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag, entity_type),
        "golden_vectors": _vectors(entity_type), "valid_from": valid_from, "change_class": change_class,
        "source_mappings": maps})
    if d.status_code != 200:
        return None, d
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        if r.status_code != 200:
            return None, r
    return did, None


def _publish(client, did, label=None, **extra):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": label or f"rpi-{did[:8]}", "changelog": "probe", **extra})


def _subject(name: str, tenant=TA) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=tenant, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    return sid


def _fact(client, sid, key, value, actor=CCO, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=40 if not change else 1)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "changed"}
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(actor), json=body)
    assert r.status_code == 200, r.text


def _evaluated(client, name, entity_type="CREDIT_INSTITUTION", tenant=TA, actor=CCO, evaluate=True) -> dict:
    sid = _subject(name, tenant)
    _fact(client, sid, "entity.established_in_eu", True, actor)
    _fact(client, sid, "entity.financial_entity_type", entity_type, actor)
    out = {"sid": sid}
    if evaluate:
        r = client.post("/api/applicability/evaluations", headers=H(actor),
                        json={"subject_id": sid, "obligation_definition_id": S["odef"]})
        assert r.status_code == 200, r.text
        out |= {"ev": r.json(), "lineage": (r.json()["current_determination"] or {}).get("lineage_id")}
    return out


def _evaluations(sid):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.subject_id == sid)
                          .order_by(ApplicabilityEvaluation.record_seq)).scalars().all()


def _current_det(lineage):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        return db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.lineage_id == lineage,
                                                                   ApplicabilityDetermination.record_to_seq.is_(None))).scalars().all()


def _rule_of(evaluation_id):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return db.get(ApplicabilityEvaluation, evaluation_id).rule_version_id


def _row(table, key, value):
    from app.db import admin_engine
    from app.platform import digest
    with admin_engine.connect() as c:
        r = c.execute(text(f"SELECT * FROM {table} WHERE {key} = :v"), {"v": value}).mappings().one()
    return digest({k: str(v) for k, v in r.items()})


def _events(event_type, aggregate_id=None):
    from app.db import admin_engine
    q = "SELECT aggregate_id, payload FROM platform_outbox_event WHERE event_type = :t"
    params = {"t": event_type}
    if aggregate_id:
        q += " AND aggregate_id = :a"
        params["a"] = aggregate_id
    with admin_engine.connect() as c:
        rows = c.execute(text(q + " ORDER BY record_seq"), params).all()
    return [(a, p if isinstance(p, dict) else json.loads(p)) for a, p in rows]


def _audit(action, object_id):
    from app.db import admin_engine
    with admin_engine.connect() as c:
        return c.execute(text("SELECT new_state, reason, record_seq FROM platform_audit_event WHERE action = :a AND object_id = :o"
                              " ORDER BY record_seq"), {"a": action, "o": object_id}).all()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import ControlDefinition, Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Publication Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=f"CTRL-{TAG}-RPI", title="Publication control", objective="Probe.", category="RISK_MANAGEMENT"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="RPI CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="RPI CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Publication Act",
        "locator": f"OJ L {TAG}/rpi", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()

    def requirement(key, valid_from="2025-01-01", verify=True):
        r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
            "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
            "title": "Register requirement", "locator": "Art. 1", "valid_from": valid_from, "provisions": [
                {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
                 "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
                 "evidence_expectation": "Register"}]})
        assert r.status_code in (200, 201), r.text
        if verify:
            assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify",
                               headers=H(LEGAL)).status_code == 200
        return r.json()
    S["rv"] = requirement(f"RPI-{TAG}-REQ")
    S["rv_unverified"] = requirement(f"RPI-{TAG}-OTHER", verify=False)
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RPI_{TAG}", title="Publication obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True,
                                 satisfaction_template={"node_type": "CONTROL", "node_id": "c", "control_key": f"CTRL-{TAG}-RPI"})
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    did, bad = _draft(client, "r1")
    assert bad is None, bad.text
    r1 = _publish(client, did, f"rpi-{TAG}-r1")
    assert r1.status_code == 200, r1.text
    S["R1"], S["rule1"] = r1.json()["content_release"], r1.json()["rule_version"]
    # Subjects decided under R1: one adopted (APPLIES), one only proposed, one in another organisation.
    S["adopted"] = _evaluated(client, "adopted")
    a = client.post(f"/api/applicability/determinations/{S['adopted']['lineage']}/attest", headers=H(CCO),
                    json={"decision": "ADOPT", "authority_basis": "Board RPI"})
    assert a.status_code == 200, a.text
    S["adopted"]["obligation"] = a.json()["obligation"]
    S["proposed"] = _evaluated(client, "proposed")
    S["other_org"] = _evaluated(client, "otherorg", tenant=TB, actor=CCO_B)
    return True


# ------------------------------------------------------------------------------------------------ publication gate
def test_mapping_to_unverified_requirement_is_refused_without_writes(client, world):
    rv = S["rv_unverified"]
    p = rv["provisions"][0]
    did, bad = _draft(client, "unv", mappings=[_mapping(f"sm_unv_{n}", rv, p) for n in ("root", "1", "2")])
    before = db_fingerprint()
    r = bad if did is None else _publish(client, did)
    assert r.status_code == 409, r.text
    assert "REQUIREMENT_NOT_RELEASABLE" in r.text or "RULE_SOURCE_MAPPING_NOT_VERIFIED" in r.text, r.text
    if did is not None:
        assert db_fingerprint() == before


@pytest.mark.parametrize("over, code", [({"requirement_version_id": None, "provision_id": None}, "RULE_SOURCE_MAPPING_UNGROUNDED"),
                                        ({"source_fingerprint": "f" * 64}, "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH")])
def test_ungrounded_or_mismatched_mappings_block_publication(client, world, over, code):
    p = S["rv"]["provisions"][0]
    did, bad = _draft(client, f"bad{code[-6:].lower()}", mappings=[_mapping(f"sm_bad{code[-6:].lower()}_{n}", S["rv"], p, **over)
                                                                    for n in ("root", "1", "2")])
    r = bad if did is None else _publish(client, did)
    assert r.status_code == 409 and code in r.text, r.text


def test_altered_requirement_reference_blocks_publication_and_leaves_nothing(client, world):
    did, bad = _draft(client, "alt")
    assert bad is None, bad.text
    with _tampered("catalog_requirement_version", "requirement_version_id", S["rv"]["requirement_version_id"],
                   lifecycle="REVIEW_REQUIRED"):
        before = db_fingerprint()
        r = _publish(client, did)
        assert r.status_code == 409 and "RELEASE_CONTENT_NOT_VERIFIED" in r.text, r.text
        assert db_fingerprint() == before


def test_failure_during_publication_rolls_everything_back(client, world, monkeypatch):
    from app.engines import catalog
    did, bad = _draft(client, "fail")
    assert bad is None, bad.text

    def boom(*a, **k):
        raise HTTPException(503, "simulated failure after the rule version was written")
    monkeypatch.setattr(catalog, "publish_bound", boom)
    before = db_fingerprint()
    assert _publish(client, did).status_code == 503
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ publication and impact
def test_correction_release_preserves_history_and_reports_aggregate_impact(client, world):
    rule1_row = _row("catalog_rule_version", "rule_version_id", S["rule1"]["rule_version_id"])
    e_adopted = S["adopted"]["ev"]["evaluation_id"]
    e_row = _row("applicability_evaluation", "evaluation_id", e_adopted)
    obl_lineage = S["adopted"]["obligation"]["lineage_id"]
    seq_before = client.get("/api/watermark", headers=H(CCO)).json()["record_seq"]
    dossier_before = client.get(f"/api/obligations/{obl_lineage}", params={"as_of_record_seq": seq_before}, headers=H(CCO)).json()

    did, bad = _draft(client, "c2", entity_type="PAYMENT_INSTITUTION", change_class="CORRECTION")
    assert bad is None, bad.text
    r = _publish(client, did, f"rpi-{TAG}-r2", correction_reason="Applied to credit institutions in error.")
    assert r.status_code == 200, r.text
    out = r.json()
    S["R2"], S["rule2"] = out["content_release"], out["rule_version"]
    assert out["rule_version"]["version"] == 2

    impact = out["impact"]
    assert impact["status"] == "COMPLETE" and impact["rule_version_id"] == S["rule2"]["rule_version_id"]
    assert impact["targeted"] == impact["evaluated"] == out["impact_reevaluations"] == 3
    assert impact["transitions"] == {"APPLIES->DOES_NOT_APPLY": 3}
    assert impact["adopted_determination_conflicts"] == 1 and impact["failed"] == {}
    blob = json.dumps(impact)
    assert not any(x in blob for x in (TA, TB, S["adopted"]["sid"], S["other_org"]["sid"]))  # APP-REQ-014: aggregate only

    # Earlier records are unchanged and still resolve to rule 1 / R1.
    assert _row("catalog_rule_version", "rule_version_id", S["rule1"]["rule_version_id"]) == rule1_row
    assert _row("applicability_evaluation", "evaluation_id", e_adopted) == e_row
    assert _rule_of(e_adopted) == S["rule1"]["rule_version_id"]
    # New evaluations are on rule 2, in each subject's own organisation.
    for x, tenant in ((S["adopted"], TA), (S["proposed"], TA), (S["other_org"], TB)):
        latest = _evaluations(x["sid"])[-1]
        assert latest.rule_version_id == S["rule2"]["rule_version_id"] and latest.tenant_id == tenant
        assert latest.trigger == "RULE_CHANGED" and latest.outcome == "DOES_NOT_APPLY"
    # Adopted: flagged for review, never rewritten (APP §10); proposed: a refreshed proposal; no obligation was invented.
    [adopted] = _current_det(S["adopted"]["lineage"])
    assert adopted.state == "ADOPTED" and adopted.needs_review and adopted.evaluation_id == e_adopted
    [proposed] = _current_det(S["proposed"]["lineage"])
    assert proposed.state == "PROPOSED" and _rule_of(proposed.evaluation_id) == S["rule2"]["rule_version_id"]
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        assert db.execute(select(ObligationInstance).where(ObligationInstance.subject_id == S["proposed"]["sid"])).first() is None

    # Events: published, corrected, and rule 1 superseded for new binding (ZA-EVT-001), each once.
    cr = S["R2"]["content_release_id"]
    assert len(_events("content_release.published", cr)) == 1 and len(_events("content_release.corrected", cr)) == 1
    [(_, sup)] = _events("rule.superseded", S["rule1"]["rule_version_id"])
    assert sup["superseded_by_rule_version_id"] == S["rule2"]["rule_version_id"] and sup["content_release_id"] == cr
    # Audit: the publication and the impact outcome, at the publication's record sequence.
    [(new, _, seq_pub)] = _audit("content.publish", cr)
    [(status, reason, seq_imp)] = _audit("content.impact", cr)
    assert new == "PUBLISHED" and status == "COMPLETE" and seq_imp == seq_pub and "evaluated 3" in reason

    # The obligation's history as known before the release is unchanged and reproducible.
    again = client.get(f"/api/obligations/{obl_lineage}", params={"as_of_record_seq": seq_before}, headers=H(CCO)).json()
    for k in ("obligation", "decision_history", "regulatory_provenance", "expression_versions"):
        assert again[k] == dossier_before[k], k
    assert again["decision_history"]["integrity"]["status"] == "INTACT"


def test_duplicate_publication_is_refused(client, world):
    did, bad = _draft(client, "dup")
    assert bad is None, bad.text
    assert _publish(client, did).status_code == 200
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409
    assert db_fingerprint() == before


def test_future_effective_rule_is_reported_not_yet_effective(client, world):
    """TIM-REQ-026: published content that is not yet valid does not bind current applicability; the impact report says
    so instead of counting the subjects as evaluated under the new rule."""
    future = (date.today() + timedelta(days=400)).isoformat()
    did, bad = _draft(client, "fut", valid_from=future)
    assert bad is None, bad.text
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    impact = r.json()["impact"]
    assert impact["not_yet_effective"] == impact["targeted"] and impact["transitions"] == {}
    assert impact["status"] == "COMPLETE"


def test_impact_failures_are_reported_incomplete_never_no_impact(client, world, monkeypatch):
    from app.engines import applicability as appl
    real = appl.evaluate
    victim = S["proposed"]["sid"]

    def flaky(db, **kw):
        if kw["subject"].subject_id == victim and kw.get("trigger") == "RULE_CHANGED":
            raise HTTPException(409, {"error": "SCHEMA_INCOMPATIBLE", "message": "simulated"})
        return real(db, **kw)
    monkeypatch.setattr(appl, "evaluate", flaky)
    did, bad = _draft(client, "flk")
    assert bad is None, bad.text
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    impact = r.json()["impact"]
    assert impact["status"] == "INCOMPLETE" and impact["failed"] == {"SCHEMA_INCOMPATIBLE": 1}
    assert impact["evaluated"] == impact["targeted"] - 1
    [(status, reason, _)] = _audit("content.impact", r.json()["content_release"]["content_release_id"])
    assert status == "INCOMPLETE" and "SCHEMA_INCOMPATIBLE" in reason


def test_older_knowledge_evaluation_never_moves_the_determination_back(client, world):
    """A requested evaluation at an earlier record cutoff is history, not new knowledge: it is stored, but the current
    determination stays on the newest-knowledge evaluation and attestation still accepts it (TIM-REQ-006/007)."""
    x = _evaluated(client, "oldknow")
    cutoff_old = x["ev"]["record_cutoff_seq"]
    _fact(client, x["sid"], "entity.established_in_eu", False, change=True)
    [cur] = _current_det(x["lineage"])
    newest = cur.evaluation_id
    assert newest != x["ev"]["evaluation_id"]
    r = client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": x["sid"], "obligation_definition_id": S["odef"], "record_cutoff_seq": cutoff_old,
        "effective_at": (NOW() - timedelta(days=2)).isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["evaluation_id"] not in (newest, x["ev"]["evaluation_id"])  # a new, older-knowledge evaluation
    [cur] = _current_det(x["lineage"])
    assert cur.evaluation_id == newest
    a = client.post(f"/api/applicability/determinations/{x['lineage']}/attest", headers=H(CCO),
                    json={"decision": "ADOPT", "authority_basis": "Board RPI", "evaluation_id": newest})
    assert a.status_code == 200, a.text


def test_tenant_isolation_of_impact_results(client, world):
    e = _evaluations(S["other_org"]["sid"])[-1]
    assert client.get(f"/api/applicability/evaluations/{e.evaluation_id}", headers=H(CCO)).status_code == 404
    assert client.get(f"/api/applicability/evaluations/{e.evaluation_id}", headers=H(CCO_B)).status_code == 200


def test_published_rule_versions_cannot_be_changed_by_the_application_role(client, world):
    from app.db import IS_POSTGRES, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from sqlalchemy.exc import DBAPIError
    with engine.connect() as c, pytest.raises(DBAPIError):
        c.execute(text("UPDATE catalog_rule_version SET ast_hash = :h WHERE rule_version_id = :r"),
                  {"h": "0" * 64, "r": S["rule1"]["rule_version_id"]})


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and advisory locks; exact reason required by the CI gate
    return True


def _session(tenant, actor):
    from app.db import SessionLocal, set_principal
    db = SessionLocal()
    set_principal(db, tenant, actor)
    return db


def _ctx(db, actor):
    from app.models import Actor
    from app.security import Ctx
    return Ctx(db=db, actor=db.get(Actor, actor))


def _publish_direct(did):
    from app.routers.rules import PublishIn, publish
    with _session(None, RELEASE) as db:
        return publish(did, PublishIn(label=f"rpi-{did[:8]}", changelog="race"), _ctx(db, RELEASE))["rule_version"]["version"]


def _attest_direct(lineage):
    from app.engines import applicability as appl
    with _session(TA, CCO) as db:
        appl.attest(db, ctx=_ctx(db, CCO), determination_lineage_id=lineage, decision="ADOPT", authority_basis="Board RPI")
        db.commit()


def _evaluate_direct(sid):
    from app.engines import applicability as appl
    from app.models import ObligationDefinition, Subject
    with _session(TA, CCO) as db:
        ev = appl.evaluate(db, tenant_id=TA, subject=db.get(Subject, sid), odef=db.get(ObligationDefinition, S["odef"]),
                           effective_at=NOW(), record_cutoff_seq=None, trigger="EXPLICIT_REQUEST", actor_id=CCO)
        db.commit()
        return ev.rule_version_id


def _fact_direct(sid, value):
    from app.routers.applicability import FactIn, assert_fact_ep
    with _session(TA, CCO) as db:
        assert_fact_ep(sid, FactIn(fact_key="entity.financial_entity_type", value=value,
                                   valid_from=NOW() - timedelta(days=1), change_kind="PROSPECTIVE_CHANGE", reason="race"),
                       _ctx(db, CCO))


def _race(monkeypatch, module, target, first, second, *, after=True):
    """Thread A runs `first` and is held in `module.target` (after it returns when `after`, else before it runs); thread
    B then runs `second`. Returns both results and whether B was still waiting while A was held."""
    held, release = threading.Event(), threading.Event()
    real = getattr(module, target)

    def holding(*a, **k):
        if threading.current_thread().name != "A":
            return real(*a, **k)
        r = real(*a, **k) if after else None
        held.set()
        assert release.wait(30)
        return r if after else real(*a, **k)
    monkeypatch.setattr(module, target, holding)
    out = {}

    def run(name, fn):
        try:
            out[name] = ("ok", fn())
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001 - reported by the assertions (a deadlock surfaces here)
            out[name] = ("error", repr(e)[:300])
    a = threading.Thread(target=run, args=("A", first), name="A")
    a.start()
    assert held.wait(60), f"thread A never reached {target}: {out}"
    b = threading.Thread(target=run, args=("B", second), name="B")
    b.start()
    b.join(2.0)
    waited = b.is_alive()
    release.set()
    a.join(90)
    b.join(90)
    return out, waited


def test_fact_change_and_attestation_of_one_scope_never_deadlock(client, world, pg, monkeypatch):
    """A fact change re-evaluates its dependents. It takes their decision locks before the record sequence, as attest
    does, so the two serialise instead of each holding what the other needs."""
    from app.engines import applicability as appl
    x = _evaluated(client, "dl-fact")
    out, waited = _race(monkeypatch, appl, "lock_decision", lambda: _attest_direct(x["lineage"]),
                        lambda: _fact_direct(x["sid"], "PAYMENT_INSTITUTION"))
    assert out["A"][0] == "ok" and out["B"][0] == "ok", out
    assert waited
    [cur] = _current_det(x["lineage"])
    assert cur.state == "ADOPTED" and cur.needs_review  # the re-evaluation after the adoption flagged it


def test_publication_and_attestation_never_deadlock(client, world, pg, monkeypatch):
    from app.engines import applicability as appl
    x = _evaluated(client, "dl-pub")
    did, bad = _draft(client, "dlp")
    assert bad is None, bad.text
    out, waited = _race(monkeypatch, appl, "lock_decision", lambda: _attest_direct(x["lineage"]),
                        lambda: _publish_direct(did))
    assert out["A"][0] == "ok" and out["B"][0] == "ok", out
    assert waited
    assert _rule_of(_evaluations(x["sid"])[-1].evaluation_id) != x["ev"]["rule_version_id"]  # re-evaluated after adoption


def test_evaluation_in_progress_finishes_before_publication_and_is_reevaluated(client, world, pg, monkeypatch):
    """An evaluation that resolved the previous rule commits before the publication reads its targets, so the
    publication re-evaluates it; it never commits an old-rule result after the new rule's impact was computed."""
    from app.engines import applicability as appl
    x = _evaluated(client, "race-eval", evaluate=False)
    did, bad = _draft(client, "rev")
    assert bad is None, bad.text
    out, waited = _race(monkeypatch, appl, "lock_decision", lambda: _evaluate_direct(x["sid"]), lambda: _publish_direct(did))
    assert out["A"][0] == "ok" and out["B"][0] == "ok", out
    assert waited
    evs = _evaluations(x["sid"])
    assert len(evs) == 2 and evs[0].rule_version_id == out["A"][1] and evs[1].trigger == "RULE_CHANGED"
    assert evs[1].rule_version_id != evs[0].rule_version_id
    from app.models import RuleVersion
    from app.db import AdminSession
    with AdminSession() as db:
        assert db.get(RuleVersion, evs[1].rule_version_id).version == out["B"][1]


def test_concurrent_publications_get_distinct_versions(client, world, pg, monkeypatch):
    from app.engines import applicability as appl
    d1, bad1 = _draft(client, "cp1")
    d2, bad2 = _draft(client, "cp2")
    assert bad1 is None and bad2 is None
    out, waited = _race(monkeypatch, appl, "lock_decisions", lambda: _publish_direct(d1), lambda: _publish_direct(d2))
    assert out["A"][0] == "ok" and out["B"][0] == "ok", out
    assert waited and out["B"][1] == out["A"][1] + 1


def test_concurrent_publication_of_one_draft_publishes_once(client, world, pg, monkeypatch):
    from app.engines import applicability as appl
    did, bad = _draft(client, "cp3")
    assert bad is None
    out, waited = _race(monkeypatch, appl, "lock_decisions", lambda: _publish_direct(did), lambda: _publish_direct(did))
    assert out["A"][0] == "ok" and out["B"][0] == 409, out
    assert waited
    assert len(_events("content_release.published")) == len({a for a, _ in _events("content_release.published")})
