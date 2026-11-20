"""A shadow impact authorises the publication of the change it measured, and no other.

Controlling text:
  * ZA-APP-001 §10 "Shadow evaluation before rule release": step 3 "Compare current released outcome vs candidate
    outcome"; step 7 "Signed content release records shadow summary"; APP-REQ-013 "Rule changes MUST be
    shadow-evaluated before publication against affected subjects where customer state exists".
  * ZA-RUL-001 §12 executable artifact hash (rules.ast_hash, stored on the RuleVersion and signed); §15 "Signature
    readiness: Canonical artifact and dependency hashes frozen"; RUL-REQ-019 (executable outcome-affecting changes
    undergo shadow impact before publication); RUL-REQ-020 publication fails closed.

Defect corrected: the publication gate checked only that a shadow report existed. Two drafts shadowed against the same
released version could both publish: once the first was released, the second published on a comparison with a version
it no longer superseded, and that stale summary was recorded on its release. A report also named nothing about the
executable it evaluated. Pinned here: the shadow route records its basis (the executable artifact hash evaluated and the
version then latest released, the one a release supersedes); publication accepts a report only while both still hold
and otherwise answers the existing "Shadow impact required before publication" refusal, writing nothing; run again, the
shadow impact makes the draft publishable. The binding applies whenever a report is recorded; it changes nothing for a
draft without one.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from conftest import H

TAG = secrets.token_hex(3)
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"SRB-{TAG.upper()}-REG"
F = f"srb{TAG}.flag"
S: dict = {}


def _body(value: bool, change_class: str = "EXECUTABLE_OUTCOME") -> dict:
    """APPLIES iff F equals `value`; a fresh node-ID tag, so every body is its own executable."""
    tag, p = f"b{secrets.token_hex(3)}", S["requirement"]["provisions"][0]
    mapping = {"source_mapping_id": f"sm_{tag}", "requirement_version_id": S["requirement"]["requirement_version_id"],
               "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
               "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}

    def vec(vid, cls, fact, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2026-06-01",
                "facts": {F: fact}, "expected_outcome": expected}
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": change_class, "source_mappings": [mapping],
            "ast": {"node_type": "PREDICATE", "node_id": f"{tag}_p", "operator": "EQ", "source_mappings": [f"sm_{tag}"],
                    "args": [{"node_type": "FACT_REF", "node_id": f"{tag}_pf", "fact": F},
                             {"node_type": "CONST", "node_id": f"{tag}_pc", "type": "BOOLEAN", "value": value}]},
            "golden_vectors": [vec("P", "POSITIVE", value, "APPLIES"), vec("N", "NEGATIVE", not value, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]}


def _reviewed(client, body: dict, *, shadow: bool = True) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    if shadow:
        _shadow(client, did)
    return did


def _shadow(client, did) -> dict:
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 200, r.text
    return r.json()


def _publish(client, did):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"srb-{secrets.token_hex(4)}", "changelog": "shadow binding probe"})


def _latest_version_id() -> str | None:
    from app.db import AdminSession
    from app.models import RuleVersion
    with AdminSession() as db:
        rows = db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == S["odef"])
                          .order_by(RuleVersion.version.desc())).scalars().all()
        return rows[0].rule_version_id if rows else None


def _draft_row(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        db.expunge(d)
        return d


def _set_below_the_api(did, **fields):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        for k, v in fields.items():
            setattr(d, k, v)
        db.commit()


def _refused_and_nothing_written(client, did):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and "Shadow impact required before publication" in r.text, r.text
    assert "run it again" in r.json()["detail"]
    assert db_fingerprint() == before and _draft_row(did).status == "VERIFIED"


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(FactDefinition(key=F, label=F, data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Instrument(identifier=INSTR, title="Binding Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Binding Act",
        "locator": f"OJ L {TAG}/srb", "instrument_identifier": INSTR, "raw_text": "Entities shall keep a ledger."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"SRB-{TAG}-REQ",
        "title": "Ledger requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep ledger",
             "regulated_object": "ledger", "timing": "continuous", "normalized_meaning": "Keep a ledger.",
             "evidence_expectation": "Ledger"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SRB_{TAG.upper()}", title="Binding probe", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv.json()["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S.update(source=src.json(), requirement=rv.json(), odef=o.obligation_definition_id)
    first = _reviewed(client, _body(True))
    S["first_basis"] = _draft_row(first).shadow_report["basis"]
    r = _publish(client, first)
    assert r.status_code == 200, r.text
    S["first_release"] = r.json()
    return S


# ------------------------------------------------------------------------------------------------------------ basis
def test_a_shadow_report_records_the_executable_and_the_version_it_supersedes(client, world):
    first = world["first_release"]
    # The first release superseded nothing; its report names the executable it released, and is the summary recorded.
    assert world["first_basis"] == {"executable_ast_hash": first["rule_version"]["ast_hash"],
                                    "superseded_rule_version_id": None}
    assert first["content_release"]["shadow_summary"]["basis"] == world["first_basis"]
    did = _reviewed(client, _body(False))
    basis = _draft_row(did).shadow_report["basis"]
    assert basis["superseded_rule_version_id"] == _latest_version_id() == first["rule_version"]["rule_version_id"]
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    assert basis["executable_ast_hash"] == r.json()["rule_version"]["ast_hash"]
    assert r.json()["content_release"]["shadow_summary"]["basis"] == basis


# ------------------------------------------------------------------------------------------------------------ refusals
def test_a_shadow_taken_before_a_newer_release_cannot_publish_until_run_again(client, world):
    """The defect: A and B are shadowed against the same released version; A is released; B used to publish on its
    comparison with the version A replaced."""
    a, b = _reviewed(client, _body(True)), _reviewed(client, _body(False))
    assert _draft_row(a).shadow_report["basis"]["superseded_rule_version_id"] == \
        _draft_row(b).shadow_report["basis"]["superseded_rule_version_id"]
    ra = _publish(client, a)
    assert ra.status_code == 200, ra.text
    _refused_and_nothing_written(client, b)
    rerun = _shadow(client, b)
    assert rerun["basis"]["superseded_rule_version_id"] == ra.json()["rule_version"]["rule_version_id"]
    rb = _publish(client, b)
    assert rb.status_code == 200, rb.text
    assert rb.json()["rule_version"]["version"] == ra.json()["rule_version"]["version"] + 1
    assert rb.json()["content_release"]["shadow_summary"]["basis"]["superseded_rule_version_id"] == \
        ra.json()["rule_version"]["rule_version_id"]


def test_a_report_for_another_executable_is_refused(client, world):
    """The draft's executable changed below the API after its shadow (no edit route reset the report): the report is
    not for what would be released."""
    did = _reviewed(client, _body(True))
    ast = _draft_row(did).ast
    ast["args"][0]["node_id"] += "_renamed"  # outcome-neutral, but another executable artifact
    _set_below_the_api(did, ast=ast)
    _refused_and_nothing_written(client, did)
    _shadow(client, did)
    assert _publish(client, did).status_code == 200


def test_a_report_without_a_basis_is_refused(client, world):
    """A report recorded before the basis was kept proves neither what it evaluated nor against what (fail closed)."""
    did = _reviewed(client, _body(False))
    report = {k: v for k, v in _draft_row(did).shadow_report.items() if k != "basis"}
    _set_below_the_api(did, shadow_report=report)
    _refused_and_nothing_written(client, did)
    for broken in ({"executable_ast_hash": None, "superseded_rule_version_id": _latest_version_id()}, "not an object"):
        _set_below_the_api(did, shadow_report={**report, "basis": broken})
        _refused_and_nothing_written(client, did)
    _shadow(client, did)
    assert _publish(client, did).status_code == 200


def test_a_stale_report_is_refused_whatever_the_change_class(client, world):
    """A recorded summary is signed into the release, so a stale one is refused even for a class the gate does not
    require a shadow impact for (its waiver itself is unchanged: see the next test)."""
    did = _reviewed(client, _body(True, "EXECUTABLE_NON_OUTCOME"))
    assert _publish(client, _reviewed(client, _body(False))).status_code == 200  # a newer release
    _refused_and_nothing_written(client, did)


def test_a_draft_without_a_report_is_judged_as_before(client, world):
    did = _reviewed(client, _body(True, "EXECUTABLE_NON_OUTCOME"), shadow=False)
    r = _publish(client, did)  # the declared-class waiver (an open RUL owner decision) is not changed here
    assert r.status_code == 200 and r.json()["content_release"]["shadow_summary"] is None, r.text
    did = _reviewed(client, _body(False), shadow=False)  # a class that needs one, without one: the existing refusal
    r = _publish(client, did)
    assert r.status_code == 409 and "Shadow impact required before publication" in r.text
