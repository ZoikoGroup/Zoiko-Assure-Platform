"""ZA-RUL-001 §08 source-mapping field rules, enforced by the rule-publication gate.

  mapping_type             "DIRECT_TEXT, INTERPRETIVE, DEFINitional, CROSS_REFERENCE, TRANSITIONAL or approved controlled
                           code" (no further code is approved; the printed "DEFINitional" is DEFINITIONAL).
  interpretation_rationale "Required when mapping is not a direct mechanical encoding."
  authoritative_reference  "Article/section/paragraph locator or equivalent authoritative citation."

RUL-REQ-020 (publication fails closed) and §15 "Source completeness". A refusal is HTTP 409 RELEASE_CONTENT_NOT_VERIFIED
listing every problem, and writes nothing. reviewer, review_date, scope_note and rights_class are out of scope (they await
the RUL specification owner), as is mapping scope.
"""
from __future__ import annotations

import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from conftest import H
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"SMF-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TYPES = ("DIRECT_TEXT", "INTERPRETIVE", "DEFINITIONAL", "CROSS_REFERENCE", "TRANSITIONAL")
NON_DIRECT = TYPES[1:]
IDS = ("root", "a", "b")  # the three source-mapping IDs the AST cites (sm_<tag>_<id>)
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


def _mapping(tag: str, mid: str, rv=None, **over) -> dict:
    """A valid DIRECT_TEXT mapping; `over` sets fields, and a value of `...` removes the field."""
    rv = rv or S["rv"]
    p = rv["provisions"][0]
    m = {"source_mapping_id": f"sm_{tag}_{mid}", "requirement_version_id": rv["requirement_version_id"],
         "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
         "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    for k, v in over.items():
        if v is ...:
            m.pop(k, None)
        else:
            m[k] = v
    return m


def _draft(client, per_mapping: dict | None = None, rv=None) -> tuple[str, str]:
    """A VERIFIED, shadowed draft whose three mappings are valid except for the overrides in `per_mapping`
    ({mapping id: {field: value}}). Returns (draft_id, tag).

    Since ZA-RUL-001 §11 stage 2 resolution runs at static validation, a field, grounding or fingerprint defect is refused
    at submit-review (structured 409, the RULE_SOURCE_MAPPING_* code in its validation issues): that is asserted here.
    Such a draft is then put in the state of one verified before review resolved its mappings (VERIFIED, distinct
    reviewers, shadow done), so the tests below still exercise the publication gate itself."""
    tag = f"m{next(_n)}"
    per_mapping = per_mapping or {}
    maps = [_mapping(tag, mid, rv, **per_mapping.get(mid, {})) for mid in IDS]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    if r.status_code == 409:
        detail = r.json()["detail"]
        assert detail["message"] == "Validation errors block review" and detail["error"].startswith("RULE_SOURCE_MAPPING_"), detail
        _verified_below_the_api(did)
        return did, tag
    assert r.status_code == 200, r.text
    for actor, step in ((PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did, tag


def _verified_below_the_api(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        d.status, d.peer_reviewer_id, d.legal_reviewer_id = "VERIFIED", PEER, LEGAL
        d.shadow_report = {"buckets": {}, "completed_at": "2026-11-07T00:00:00+00:00"}
        db.commit()


def _publish(client, did):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"smf-{did[:8]}", "changelog": "probe"})


def _refused(client, did) -> list[dict]:
    """Publish and assert a structured, write-free refusal; return its problems."""
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "RELEASE_CONTENT_NOT_VERIFIED" and isinstance(detail["problems"], list), detail
    assert db_fingerprint() == before
    return detail["problems"]


def _codes(problems, mapping_id=None):
    return sorted((p["code"], p.get("field")) for p in problems if mapping_id is None or p.get("source_mapping_id") == mapping_id)


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Mapping Field Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Mapping Field Act",
        "locator": f"OJ L {TAG}/smf", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()

    def requirement(key, verify=True):
        r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
            "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
            "title": "Register requirement", "locator": "Art. 2", "valid_from": "2025-01-01", "provisions": [
                {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
                 "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
                 "evidence_expectation": "Register"}]})
        assert r.status_code in (200, 201), r.text
        if verify:
            assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify",
                               headers=H(LEGAL)).status_code == 200
        return r.json()
    S["rv"] = requirement(f"SMF-{TAG}-REQ")
    S["rv_unverified"] = requirement(f"SMF-{TAG}-DRAFT", verify=False)
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SMF_{TAG}", title="Mapping field obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


# ------------------------------------------------------------------------------------------------ accepted
@pytest.mark.parametrize("mapping_type", TYPES)
def test_every_permitted_mapping_type_publishes(client, world, mapping_type):
    over = {"mapping_type": mapping_type}
    if mapping_type != "DIRECT_TEXT":
        over["interpretation_rationale"] = f"Why this {mapping_type.lower()} mapping grounds the branch."
    did, _ = _draft(client, {mid: over for mid in IDS})
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    S.setdefault("published", []).append(r.json())


@pytest.mark.parametrize("rationale", [..., None, "", "   "])
def test_direct_text_needs_no_rationale(client, world, rationale):
    did, _ = _draft(client, {mid: {"interpretation_rationale": rationale} for mid in IDS})
    assert _publish(client, did).status_code == 200


# ------------------------------------------------------------------------------------------------ refused
@pytest.mark.parametrize("value, code", [(..., "RULE_SOURCE_MAPPING_TYPE_MISSING"), (None, "RULE_SOURCE_MAPPING_TYPE_MISSING"),
                                         ("", "RULE_SOURCE_MAPPING_TYPE_MISSING"), ("   ", "RULE_SOURCE_MAPPING_TYPE_MISSING"),
                                         ("DEFINitional", "RULE_SOURCE_MAPPING_TYPE_INVALID"),
                                         ("direct_text", "RULE_SOURCE_MAPPING_TYPE_INVALID"),
                                         ("DIRECT", "RULE_SOURCE_MAPPING_TYPE_INVALID"),
                                         ("EDITORIAL", "RULE_SOURCE_MAPPING_TYPE_INVALID"),
                                         (7, "RULE_SOURCE_MAPPING_TYPE_INVALID")])
def test_missing_or_unknown_mapping_type_is_refused(client, world, value, code):
    did, tag = _draft(client, {"a": {"mapping_type": value}})
    problems = _refused(client, did)
    assert _codes(problems) == [(code, "mapping_type")]
    [p] = problems
    assert p["source_mapping_id"] == f"sm_{tag}_a" and p["mapping_index"] == 1


@pytest.mark.parametrize("value", [..., None, "", "   ", 12])
def test_blank_authoritative_reference_is_refused(client, world, value):
    did, tag = _draft(client, {"b": {"authoritative_reference": value}})
    problems = _refused(client, did)
    assert _codes(problems) == [("RULE_SOURCE_MAPPING_REFERENCE_REQUIRED", "authoritative_reference")]
    assert problems[0]["source_mapping_id"] == f"sm_{tag}_b"


@pytest.mark.parametrize("mapping_type", NON_DIRECT)
@pytest.mark.parametrize("rationale", [..., None, "", "  \t "])
def test_non_direct_mapping_without_rationale_is_refused(client, world, mapping_type, rationale):
    did, tag = _draft(client, {"root": {"mapping_type": mapping_type, "interpretation_rationale": rationale}})
    problems = _refused(client, did)
    assert _codes(problems) == [("RULE_SOURCE_MAPPING_RATIONALE_REQUIRED", "interpretation_rationale")]
    assert problems[0]["mapping_type"] == mapping_type and problems[0]["source_mapping_id"] == f"sm_{tag}_root"


def test_every_invalid_mapping_and_field_is_reported_together(client, world):
    did, tag = _draft(client, {"root": {"mapping_type": "GUESS"},
                               "a": {"authoritative_reference": " "},
                               "b": {"mapping_type": "INTERPRETIVE", "interpretation_rationale": "", "authoritative_reference": ...}})
    problems = _refused(client, did)
    assert _codes(problems, f"sm_{tag}_root") == [("RULE_SOURCE_MAPPING_TYPE_INVALID", "mapping_type")]
    assert _codes(problems, f"sm_{tag}_a") == [("RULE_SOURCE_MAPPING_REFERENCE_REQUIRED", "authoritative_reference")]
    assert _codes(problems, f"sm_{tag}_b") == [("RULE_SOURCE_MAPPING_RATIONALE_REQUIRED", "interpretation_rationale"),
                                                 ("RULE_SOURCE_MAPPING_REFERENCE_REQUIRED", "authoritative_reference")]
    assert len(problems) == 4


def test_field_problems_are_reported_with_the_existing_grounding_problems(client, world):
    did, tag = _draft(client, {"a": {"source_fingerprint": "f" * 64, "mapping_type": "TRANSITIONAL"}})
    problems = _refused(client, did)
    codes = {p["code"] for p in problems if p.get("source_mapping_id") == f"sm_{tag}_a"}
    assert codes == {"RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH", "RULE_SOURCE_MAPPING_RATIONALE_REQUIRED"}


# ------------------------------------------------------------------------------------------------ existing gates unchanged
@pytest.mark.parametrize("case, code", [("fingerprint", "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH"),
                                        ("ungrounded", "RULE_SOURCE_MAPPING_UNGROUNDED"),
                                        ("unverified", "RULE_SOURCE_MAPPING_NOT_VERIFIED")])
def test_existing_content_gates_still_refuse_field_valid_mappings(client, world, case, code):
    if case == "unverified":
        did, _ = _draft(client, rv=S["rv_unverified"])
    else:
        over = {"source_fingerprint": "e" * 64} if case == "fingerprint" else {"requirement_version_id": None, "provision_id": None}
        did, _ = _draft(client, {mid: over for mid in IDS})
    problems = _refused(client, did)
    assert code in {p["code"] for p in problems}
    assert not any(p["code"].startswith(("RULE_SOURCE_MAPPING_TYPE", "RULE_SOURCE_MAPPING_RATIONALE",
                                         "RULE_SOURCE_MAPPING_REFERENCE")) for p in problems)


def test_refusal_changes_no_release_audit_event_or_record_sequence(client, world):
    from app.db import admin_engine
    did, _ = _draft(client, {"root": {"mapping_type": "NOPE"}})

    def state():
        with admin_engine.connect() as c:
            return {"releases": c.execute(text("SELECT count(*) FROM catalog_content_release")).scalar(),
                    "rule_versions": c.execute(text("SELECT count(*) FROM catalog_rule_version")).scalar(),
                    "audit": c.execute(text("SELECT count(*) FROM platform_audit_event")).scalar(),
                    "outbox": c.execute(text("SELECT count(*) FROM platform_outbox_event")).scalar(),
                    "record_seq": c.execute(text("SELECT current_seq FROM platform_record_sequence")).scalar(),
                    "draft": c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).scalar()}
    before = state()
    _refused(client, did)
    assert state() == before and before["draft"] == "VERIFIED"


def test_previously_published_releases_are_untouched(client, world):
    from app.db import admin_engine
    from app.platform import digest
    published = S["published"]

    def rows():
        out = []
        with admin_engine.connect() as c:
            for p in published:
                for t, k, v in (("catalog_rule_version", "rule_version_id", p["rule_version"]["rule_version_id"]),
                                ("catalog_content_release", "content_release_id", p["content_release"]["content_release_id"])):
                    r = c.execute(text(f"SELECT * FROM {t} WHERE {k} = :v"), {"v": v}).mappings().one()
                    out.append(digest({x: str(y) for x, y in r.items()}))
        return out
    before = rows()
    did, _ = _draft(client, {"b": {"authoritative_reference": ""}})
    _refused(client, did)
    assert rows() == before


def test_seeded_rule_mappings_satisfy_the_field_rules(client, world):
    """The demo catalogue's released mappings already meet §08 (DIRECT_TEXT / CROSS_REFERENCE with rationales)."""
    from app.db import AdminSession
    from app.engines.catalog import mapping_field_problems
    from app.models import Instrument, ObligationDefinition, RuleVersion
    seeded = ("EU-2024-1689", "EU-2022-2554", "UK-GDPR", "EU-2022-2555", "FCA-PRIN-2A")
    with AdminSession() as db:
        rvs = db.execute(select(RuleVersion).join(ObligationDefinition, ObligationDefinition.obligation_definition_id
                                                  == RuleVersion.obligation_definition_id)
                         .join(Instrument, Instrument.instrument_id == ObligationDefinition.instrument_id)
                         .where(Instrument.identifier.in_(seeded))).scalars().all()
        maps = [(rv.rule_version_id, i, m) for rv in rvs for i, m in enumerate(rv.source_mappings or [])]
    assert rvs and maps
    assert [(rv, p) for rv, i, m in maps for p in mapping_field_problems(m, i)] == []


# ------------------------------------------------------------------------------------------------ PostgreSQL transaction
def test_refusal_holds_no_lock_and_leaves_the_draft_publishable_on_postgresql(client, world):
    """The refusal ends the publication's transaction: its draft row lock is released at once (another transaction can
    take it without waiting) and the draft, still VERIFIED, publishes once its mapping is corrected."""
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.db import AdminSession, SessionLocal, admin_engine, set_principal
    from app.models import Actor, RuleDraft
    from app.routers.rules import PublishIn, publish
    from app.security import Ctx
    did, tag = _draft(client, {"a": {"mapping_type": "WRONG"}})
    with SessionLocal() as db:
        set_principal(db, None, RELEASE)
        with pytest.raises(HTTPException) as e:
            publish(did, PublishIn(label=f"smf-{did[:8]}", changelog="pg"), Ctx(db=db, actor=db.get(Actor, RELEASE)))
        assert e.value.status_code == 409 and e.value.detail["error"] == "RELEASE_CONTENT_NOT_VERIFIED"
        db.rollback()
    with admin_engine.connect() as c:  # NOWAIT: fails immediately if any transaction still holds the row
        c.execute(text("SET lock_timeout = '1s'"))
        assert c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d FOR UPDATE NOWAIT"),
                         {"d": did}).scalar() == "VERIFIED"
        c.rollback()
    with AdminSession() as db:  # correct the mapping below the API (a draft edit would reset its reviews) and publish
        d = db.get(RuleDraft, did)
        d.source_mappings = [dict(m, mapping_type="DIRECT_TEXT") for m in d.source_mappings]
        db.commit()
    assert _publish(client, did).status_code == 200
