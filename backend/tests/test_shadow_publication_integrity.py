"""Rule shadow evaluation and publication impact integrity.

Controlling text:
  * ZA-RUL-001 §14 (which change classes need a shadow impact), §15 shadow-impact and publication gate ("Shadow
    completion: All required cells complete or governed disposition exists; no unexplained runtime invariant breach";
    fail-closed: "Any SINGLE_MATCH runtime overlap in shadow testing blocks publication", "Shadow-job infrastructure
    failures are not treated as 'no impact'"), §16 post-approval mutation ("any edit invalidates approvals/tests"),
    RUL-REQ-013 (runtime multiple-TRUE is an invariant breach), RUL-REQ-019/020.
  * ZA-APP-001 §10 shadow step 3 ("Compare current released outcome vs candidate outcome"), APP-REQ-013/014.
  * ZA-TIM-001 TIM-REQ-006/007: an evaluation requested at an earlier record cutoff is history, never the latest knowledge.

Concurrency tests (PostgreSQL 17) hold one transaction inside its critical section and show that the other waits.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint
from test_rule_publication_impact import _ctx, _publish_direct, _race, _session

TAG = secrets.token_hex(3).upper()
INSTR = f"SPI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-spi-{TAG.lower()}"
CCO = f"spi-cco-{TAG.lower()}"
S: dict = {}
AT = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
SHADOW_REQUIRED = ("EXECUTABLE_OUTCOME", "TEMPORAL", "DEPENDENCY", "CORRECTION")
SHADOW_OPTIONAL = ("EDITORIAL_METADATA", "SOURCE_MAPPING", "EXECUTABLE_NON_OUTCOME")


def _ast(tag: str, single: bool) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    root = {"node_type": "ANY" if single else "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"],
            "children": [pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
                         pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}
    if single:  # exactly one branch may be TRUE (RUL-REQ-013); a subject meeting both is a runtime invariant breach
        root["single_match"] = True
    return root


def _vectors(single: bool) -> list[dict]:
    facts = lambda eu, v: {"entity.established_in_eu": eu, "entity.financial_entity_type": v}  # noqa: E731
    other = "PAYMENT_INSTITUTION" if single else "CREDIT_INSTITUTION"
    return [{"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
             "facts": facts(True, other), "expected_outcome": "APPLIES"},
            {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
             "facts": facts(False, "PAYMENT_INSTITUTION"), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
             "facts": facts({"unknown": "MISSING_REQUIRED_FACT"}, other if not single else "PAYMENT_INSTITUTION"),
             "expected_outcome": "UNCERTAIN"}]


def _body(change_class: str = "EXECUTABLE_OUTCOME", *, single: bool = False) -> dict:
    tag, p = f"s{secrets.token_hex(3)}", S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag, single),
            "golden_vectors": _vectors(single), "valid_from": "2024-01-01", "valid_to": None,
            "decision_table": None, "change_class": change_class, "source_mappings": maps}


def _review(client, did: str) -> None:
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)


def _reviewed(client, body: dict, *, shadow: bool = True) -> str:
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    _review(client, did)
    if shadow:
        assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 200
    return did


def _publish(client, did: str, change_class: str = "EXECUTABLE_OUTCOME"):
    body = {"label": f"spi-{did[:8]}", "changelog": "x"}
    if change_class == "CORRECTION":
        body["correction_reason"] = "Prior interpretation was wrong."
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json=body)


def _draft_row(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        db.expunge(d)
        return d


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Shadow Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="SPI CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Shadow Act",
        "locator": f"OJ L {TAG}/spi", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"SPI-{TAG}-REQ",
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
        o = ObligationDefinition(key=f"SPI_{TAG}", title="Shadow obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    base = _reviewed(client, _body())  # the published baseline (eu AND credit institution)
    assert _publish(client, base).status_code == 200
    return True


def _subject(client, name: str, *, facts: bool = True) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    if facts:
        _facts(client, sid)
    return sid


def _facts(client, sid: str) -> None:
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})
        assert r.status_code == 200, r.text


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _evaluate(client, sid, **extra):
    r = client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid, "obligation_definition_id": S["odef"], "effective_at": AT.isoformat(), **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ publication gate
@pytest.mark.parametrize("change_class", SHADOW_REQUIRED)
def test_a_change_class_that_needs_a_shadow_impact_is_refused_without_one(client, world, change_class):
    did = _reviewed(client, _body(change_class), shadow=False)
    before = db_fingerprint()
    r = _publish(client, did, change_class)
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before


@pytest.mark.parametrize("change_class", [c for c in SHADOW_OPTIONAL if c != "EDITORIAL_METADATA"])
def test_a_change_class_the_gate_exempts_still_publishes_without_a_shadow_impact(client, world, change_class):
    # Unchanged pending owner decisions: SOURCE_MAPPING's waiver rests on an undefined "result hash" (RUL §14), and
    # EXECUTABLE_NON_OUTCOME's "shadow policy based on risk" (RUL §14) is in tension with APP-REQ-013.
    did = _reviewed(client, _body(change_class), shadow=False)
    r = _publish(client, did, change_class)
    assert r.status_code == 200, r.text
    assert r.json()["content_release"]["shadow_summary"] is None


def test_an_editorial_release_that_changes_the_executable_is_not_exempt(client, world):
    """RUL §14: EDITORIAL_METADATA holds "only if executable artifact/hash unaffected"; this draft's AST is new, so it
    needs its shadow impact like any executable change (tests/test_editorial_change_shadow_gate.py has the full rule)."""
    did = _reviewed(client, _body("EDITORIAL_METADATA"), shadow=False)
    before = db_fingerprint()
    r = _publish(client, did, "EDITORIAL_METADATA")
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before


def test_another_drafts_shadow_impact_never_authorises_publication(client, world):
    shadowed = _reviewed(client, _body())
    unshadowed = _reviewed(client, _body(), shadow=False)
    before = db_fingerprint()
    r = _publish(client, unshadowed)
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before and _draft_row(shadowed).shadow_report is not None


def test_an_edit_discards_the_shadow_impact_and_the_re_reviewed_draft_needs_a_new_one(client, world):
    body = _body()
    did = _reviewed(client, body)
    assert client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=body).status_code == 200
    d = _draft_row(did)
    assert d.shadow_report is None and d.status == "DRAFT" and d.revision == 2
    _review(client, did)
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before
    assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 200
    assert _publish(client, did).status_code == 200


def test_the_shadow_baseline_is_the_latest_knowledge_evaluation(client, world):
    """APP §10 step 3 compares the current released outcome with the candidate's. An evaluation requested later at an
    older record cutoff is history (TIM-REQ-006/007), as the publication impact and the determination already treat
    it: it must not become the shadow baseline and turn an unchanged subject into a reported transition."""
    sid = _subject(client, "baseline", facts=False)
    old_cutoff = _seq()
    _facts(client, sid)
    assert _evaluate(client, sid)["outcome"] == "APPLIES"
    did = _reviewed(client, _body())
    first = _draft_row(did).shadow_report["transition_buckets"]
    older = _evaluate(client, sid, record_cutoff_seq=old_cutoff)  # recorded last, with older knowledge
    assert older["outcome"] == "UNCERTAIN"
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 200, r.text
    assert r.json()["transition_buckets"] == first


def test_shadow_and_impact_summaries_add_up_to_their_targets(client, world):
    sid = _subject(client, "sums")
    _evaluate(client, sid)
    did = _reviewed(client, _body())
    rep = _draft_row(did).shadow_report
    assert sum(rep["transition_buckets"].values()) + rep["execution_errors"] == rep["subjects_evaluated"]
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    i = r.json()["impact"]
    assert i["status"] == "COMPLETE" and i["targeted"] == i["evaluated"] + sum(i["failed"].values())
    assert i["evaluated"] == i["unchanged"] + sum(i["transitions"].values()) + i["not_yet_effective"]
    assert r.json()["content_release"]["shadow_summary"] == rep


def test_a_shadow_impact_with_a_runtime_invariant_breach_blocks_publication(client, world):
    """Runs after the other SQLite tests: on code without the gate the breaching rule is published and becomes the rule
    in force, which would make later evaluations of this definition fail.

    RUL §15: "Any SINGLE_MATCH runtime overlap in shadow testing blocks publication"; "no unexplained runtime invariant
    breach". The candidate passes static validation and every golden vector, but one subject meets both branches of
    its SINGLE_MATCH node: the shadow run records an execution error, and the release is refused with nothing written."""
    _subject(client, "breach")  # established in the EU and a credit institution: both branches TRUE
    did = _reviewed(client, _body(single=True))
    rep = _draft_row(did).shadow_report
    assert rep["execution_errors"] >= 1, rep
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "SHADOW_IMPACT_INCOMPLETE" and r.json()["detail"]["execution_errors"] == rep["execution_errors"]
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and row locks; exact reason required by the CI gate
    return True


def _shadow_direct(did):
    from app.routers.rules import shadow
    with _session(None, RELEASE) as db:
        return shadow(did, _ctx(db, RELEASE))


def _edit_direct(did, body):
    from app.routers.rules import DraftIn, update_draft
    with _session(None, AUTHOR) as db:
        return update_draft(did, DraftIn(**body), _ctx(db, AUTHOR))["revision"]


def test_an_edit_during_a_shadow_run_never_receives_that_runs_report(client, world, pg, monkeypatch):
    """The shadow run and the edit serialise on the draft row: the edit waits, then discards the report, so the new
    revision is never left carrying a report computed on the previous revision's content (RUL §16)."""
    from app.engines import applicability as appl
    body = _body()
    did = _reviewed(client, body, shadow=False)
    out, waited = _race(monkeypatch, appl, "shadow_impact", lambda: _shadow_direct(did), lambda: _edit_direct(did, body))
    assert out["A"][0] == "ok" and out["B"] == ("ok", 2), out
    d = _draft_row(did)
    assert d.revision == 2 and d.status == "DRAFT" and d.shadow_report is None
    assert waited


def test_a_shadow_run_and_a_publication_of_one_draft_serialise(client, world, pg, monkeypatch):
    """A publication waits for a shadow run of the same draft in progress and releases with that run's report: the
    release's shadow summary is the draft's report, never one replaced after the release was signed."""
    from app.engines import applicability as appl
    did = _reviewed(client, _body())
    out, waited = _race(monkeypatch, appl, "shadow_impact", lambda: _shadow_direct(did), lambda: _publish_direct(did))
    assert out["A"][0] == "ok" and out["B"][0] == "ok", out
    from app.db import AdminSession
    from app.models import ContentRelease, RuleVersion
    with AdminSession() as db:
        rv = db.execute(select(RuleVersion).where(RuleVersion.draft_id == did)).scalars().one()
        summary = db.get(ContentRelease, rv.content_release_id).shadow_summary
    d = _draft_row(did)
    assert d.status == "PUBLISHED" and summary == d.shadow_report == out["A"][1]
    assert waited


def test_an_edit_during_a_publication_waits_and_is_refused(client, world, pg, monkeypatch):
    from app.engines import applicability as appl
    body = _body()
    did = _reviewed(client, body)
    out, waited = _race(monkeypatch, appl, "lock_decisions", lambda: _publish_direct(did), lambda: _edit_direct(did, body))
    assert out["A"][0] == "ok" and out["B"][0] == 409, out
    assert waited
    d = _draft_row(did)
    assert d.status == "PUBLISHED" and d.revision == 1
