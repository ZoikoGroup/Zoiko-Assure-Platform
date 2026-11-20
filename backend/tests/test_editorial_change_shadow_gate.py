"""An EDITORIAL_METADATA release skips the shadow impact only if it leaves the executable artifact unchanged.

Controlling text:
  * ZA-RUL-001 §14 EDITORIAL_METADATA: "Label/help text typo with no executable/source interpretation change" — "only
    if executable artifact/hash unaffected"; §12 the executable artifact hash (rules.ast_hash: canonical AST, schema and
    operator-set versions, set hashes), stored on the RuleVersion and signed in the release manifest.
  * RUL-REQ-019 "Any executable outcome-affecting rule change MUST undergo shadow impact before publication where
    affected customer state exists"; RUL-REQ-020 publication fails closed; APP-REQ-013.

Defect corrected: the publication gate exempted a draft from the shadow impact on its declared change class alone, so a
rule whose executable changed (here: its outcome) published with no shadow impact by being labelled EDITORIAL_METADATA.
Pinned here: the exemption holds only when the release's executable artifact hash equals that of the version it
supersedes (the latest version of the definition, named in the manifest); otherwise the existing "Shadow impact
required before publication" refusal applies, nothing is written, and a shadow impact makes it publishable. A first
release supersedes nothing, so it cannot be editorial. SOURCE_MAPPING and EXECUTABLE_NON_OUTCOME are not changed here
(undefined "result hash"; risk-based shadow policy in tension with APP-REQ-013): their owners decide.
"""
from __future__ import annotations

import copy
import secrets

import pytest
from sqlalchemy import select

from conftest import H

TAG = secrets.token_hex(3)
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"ECS-{TAG.upper()}-REG"
F = f"ecs{TAG}.flag"
S: dict = {}


def _ast(tag: str, value: bool, mapping: str) -> dict:
    """APPLIES iff F equals `value`: the constant is the outcome-affecting part."""
    return {"node_type": "PREDICATE", "node_id": f"{tag}_p", "operator": "EQ", "source_mappings": [mapping],
            "args": [{"node_type": "FACT_REF", "node_id": f"{tag}_pf", "fact": F},
                     {"node_type": "CONST", "node_id": f"{tag}_pc", "type": "BOOLEAN", "value": value}]}


def _vectors(value: bool) -> list[dict]:
    def vec(vid, cls, fact, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2026-06-01",
                "facts": {F: fact}, "expected_outcome": expected}
    return [vec("P", "POSITIVE", value, "APPLIES"), vec("N", "NEGATIVE", not value, "DOES_NOT_APPLY"),
            vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]


def _odef(requirement) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"ECS_{secrets.token_hex(4).upper()}", title="Editorial probe",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in requirement["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _body(odef: str, value: bool, change_class: str) -> dict:
    tag, p = f"e{secrets.token_hex(3)}", S["requirement"]["provisions"][0]
    mapping = {"source_mapping_id": f"sm_{tag}", "requirement_version_id": S["requirement"]["requirement_version_id"],
               "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
               "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    return {"obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(tag, value, mapping["source_mapping_id"]),
            "source_mappings": [mapping], "golden_vectors": _vectors(value), "valid_from": "2024-01-01",
            "change_class": change_class}


def _same_as(version: dict, change_class: str) -> dict:
    """A draft whose executable is exactly that of a published RuleVersion (its canonical AST, mappings, vectors)."""
    return {"obligation_definition_id": version["obligation_definition_id"], "authoring_mode": "AST",
            "ast": copy.deepcopy(version["canonical_ast"]), "source_mappings": version["source_mappings"],
            "golden_vectors": version["golden_vectors"], "valid_from": version["valid_from"], "change_class": change_class}


def _reviewed(client, body: dict, *, shadow: bool = False) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    if shadow:
        assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 200
    return did


def _publish(client, did: str):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"ecs-{secrets.token_hex(4)}", "changelog": "editorial probe"})


def _versions(odef: str) -> list[dict]:
    from app.db import AdminSession
    from app.models import RuleVersion
    from app.platform import to_dict
    with AdminSession() as db:
        return [to_dict(v) for v in db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == odef)
                                               .order_by(RuleVersion.version)).scalars()]


@pytest.fixture(scope="module")
def world(client):
    """A definition with a first release (F == True, shadowed) to supersede."""
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument
    with AdminSession() as db:
        db.add(FactDefinition(key=F, label=F, data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Instrument(identifier=INSTR, title="Editorial Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Editorial Act",
        "locator": f"OJ L {TAG}/ecs", "instrument_identifier": INSTR, "raw_text": "Entities shall keep a ledger."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"ECS-{TAG}-REQ",
        "title": "Ledger requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep ledger",
             "regulated_object": "ledger", "timing": "continuous", "normalized_meaning": "Keep a ledger.",
             "evidence_expectation": "Ledger"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S.update(source=src.json(), requirement=rv.json())
    S["odef"] = _odef(rv.json())
    r = _publish(client, _reviewed(client, _body(S["odef"], True, "EXECUTABLE_OUTCOME"), shadow=True))
    assert r.status_code == 200, r.text
    return S


# ------------------------------------------------------------------------------------------------------------ gate
def test_an_outcome_change_labelled_editorial_needs_its_shadow_impact(client, world):
    from test_authorization import db_fingerprint
    odef = world["odef"]
    did = _reviewed(client, _body(odef, False, "EDITORIAL_METADATA"))  # flips the outcome
    before, count = db_fingerprint(), len(_versions(odef))
    r = _publish(client, did)
    assert r.status_code == 409 and "Shadow impact required before publication" in r.text, r.text
    assert "EDITORIAL_METADATA" in r.json()["detail"]
    assert db_fingerprint() == before and len(_versions(odef)) == count  # nothing written
    # The refusal is the shadow requirement, not a new gate: with its shadow impact the same draft publishes.
    assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 200
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    assert r.json()["content_release"]["shadow_summary"] is not None


def test_an_editorial_release_with_the_same_executable_publishes_without_a_shadow_impact(client, world):
    odef = world["odef"]
    latest = _versions(odef)[-1]
    did = _reviewed(client, _same_as(latest, "EDITORIAL_METADATA"))
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    assert r.json()["content_release"]["shadow_summary"] is None
    new = _versions(odef)[-1]
    assert new["version"] == latest["version"] + 1 and new["ast_hash"] == latest["ast_hash"]


def test_the_baseline_is_the_version_superseded_not_an_earlier_one(client, world):
    """The executable of version 1 differs from the latest version's: an editorial release reverting to it changes the
    executable it supersedes, so it needs a shadow impact."""
    from test_authorization import db_fingerprint
    versions = _versions(world["odef"])
    first, latest = versions[0], versions[-1]
    assert first["ast_hash"] != latest["ast_hash"]
    did = _reviewed(client, _same_as(first, "EDITORIAL_METADATA"))
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before


def test_a_first_release_cannot_be_editorial(client, world):
    from test_authorization import db_fingerprint
    odef = _odef(world["requirement"])  # no version to supersede
    did = _reviewed(client, _body(odef, True, "EDITORIAL_METADATA"))
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before and _versions(odef) == []


def test_classes_that_always_need_a_shadow_impact_are_unchanged(client, world):
    from test_authorization import db_fingerprint
    did = _reviewed(client, _same_as(_versions(world["odef"])[-1], "EXECUTABLE_OUTCOME"))
    before = db_fingerprint()
    r = _publish(client, did)  # an unchanged executable does not exempt a class the gate never exempts
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before
