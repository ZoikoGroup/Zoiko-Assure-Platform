"""Source-mapping grounding and coverage across the rule lifecycle (RUL-REQ-008; ZA-RUL-001 §§08, 10, 11, 15, 18).

§08: a mapping's requirement_version_id / provision_id are "Exact source-grounded content objects" and its
source_fingerprint is the "fingerprint of source version"; "the release tooling must show coverage and orphaned
mappings". §11 stage 2: mappings "resolve" at static validation; §18: an ERROR "Blocks review/release".

Proved here, on SQLite and PostgreSQL 17:
  * a mapping grounded by its provision alone has its cited fingerprint checked against that provision's source
    snapshot, exactly as a mapping naming the requirement version (RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH);
  * a mapping whose cited content rests on a source snapshot whose stored text no longer matches its fingerprint is
    refused (RULE_SOURCE_MAPPING_SOURCE_NOT_GROUNDED), for both groundings;
  * both are refused at /validate, submit-review, shadow and publication, writing nothing;
  * the draft validation report shows the mapping coverage: referenced, unresolved, orphaned and ungrounded records.
    An orphaned record is reported, never an error: whether it blocks publication is an open governance decision.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from conftest import H
from test_authorization import db_fingerprint
from test_source_mapping_inheritance import VECTORS, pred

TAG = secrets.token_hex(3).upper()
INSTR = f"SMG-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
S: dict = {}
_n = iter(range(10**6))


def _source(client, title):
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": title,
        "locator": f"OJ L {TAG}/{title[:8]}", "instrument_identifier": INSTR, "raw_text": f"{title}: keep a register."})
    assert r.status_code == 200, r.text
    return r.json()


def _requirement(client, source, key):
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": key, "title": "Register",
        "locator": "Art. 2", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    """The definition's own content (intact source) and a second requirement on a separate source that tests may alter."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title=f"Grounding {INSTR}", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    S["source"], S["alterable"] = _source(client, "Grounding Act"), _source(client, "Alterable Act")
    S["rv"] = _requirement(client, S["source"], f"SMG-{TAG}-REQ")
    S["rv_alt"] = _requirement(client, S["alterable"], f"SMG-{TAG}-ALT")
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SMG_{TAG}", title="Grounding obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


def rec(tag, mid="root", rv_key="rv", source_key="source", **over) -> dict:
    """A valid DIRECT_TEXT mapping record grounded in S[rv_key]; `over` replaces fields (`...` removes one)."""
    p = S[rv_key]["provisions"][0]
    m = {"source_mapping_id": f"sm_{tag}_{mid}", "requirement_version_id": S[rv_key]["requirement_version_id"],
         "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
         "source_fingerprint": S[source_key]["fingerprint"], "mapping_type": "DIRECT_TEXT"}
    for k, v in over.items():
        if v is ...:
            m.pop(k, None)
        else:
            m[k] = v
    return m


def ast(tag, mappings=None):
    return {"node_type": "ALL", "node_id": f"{tag}_root",
            "source_mappings": mappings if mappings is not None else [f"sm_{tag}_root"],
            "children": [pred(f"{tag}_a"), pred(f"{tag}_b", fact="entity.financial_entity_type")]}


def _create(client, tag, records, ast_=None) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast_ or ast(tag), "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": records})
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


STEPS = (("submit-review", AUTHOR), ("peer-approve", PEER), ("legal-approve", LEGAL), ("shadow", RELEASE))


def _advance(client, did, upto):
    for step, actor in STEPS:
        if step == upto:
            return
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)


def _replace_mappings(did, records):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        db.get(RuleDraft, did).source_mappings = records
        db.commit()


def _draft_state(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        return d.status, d.peer_reviewer_id, d.legal_reviewer_id, d.revision


class _altered_source:
    """The 'Alterable Act' source text changed below the API (its fingerprint no longer matches), restored on exit."""

    def __enter__(self):
        from app.db import AdminSession
        from app.models import RegulatorySource
        with AdminSession() as db:
            s = db.get(RegulatorySource, S["alterable"]["source_id"])
            self.original = s.raw_text
            s.raw_text = self.original + " (altered after verification)"
            db.commit()

    def __exit__(self, *exc):
        from app.db import AdminSession
        from app.models import RegulatorySource
        with AdminSession() as db:
            db.get(RegulatorySource, S["alterable"]["source_id"]).raw_text = self.original
            db.commit()


def _codes(issues):
    return {i["code"] for i in issues if i["severity"] == "ERROR"}


# Each case: the records of a draft and the error the corrected validator reports for them.
def _provision_only_wrong_fingerprint(tag):
    return [rec(tag, requirement_version_id=..., source_fingerprint="f" * 64)]


def _provision_only_right_fingerprint(tag):
    return [rec(tag, requirement_version_id=...)]


# ------------------------------------------------------------------------------------------------ fingerprint (provision-grounded)
def test_a_provision_grounded_mapping_is_checked_against_its_sources_fingerprint(client, world):
    tag = f"fp{next(_n)}"
    bad = _create(client, tag, _provision_only_wrong_fingerprint(tag))
    rep = client.post(f"/api/rules/drafts/{bad}/validate", headers=H(AUTHOR)).json()
    assert rep["ok"] is False and _codes(rep["issues"]) == {"RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH"}
    issue = next(i for i in rep["issues"] if i["code"] == "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH")
    assert issue["source_mapping_id"] == f"sm_{tag}_root" and issue["node_id"] == f"{tag}_root"
    tag2 = f"fp{next(_n)}"
    good = _create(client, tag2, _provision_only_right_fingerprint(tag2))
    assert client.post(f"/api/rules/drafts/{good}/validate", headers=H(AUTHOR)).json()["ok"] is True
    # Unchanged: the same check on a mapping that also names its requirement version.
    tag3 = f"fp{next(_n)}"
    named = _create(client, tag3, [rec(tag3, source_fingerprint="f" * 64)])
    assert _codes(client.post(f"/api/rules/drafts/{named}/validate", headers=H(AUTHOR)).json()["issues"]) == \
        {"RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH"}


# ------------------------------------------------------------------------------------------------ grounding (altered source)
@pytest.mark.parametrize("grounding", ["requirement_version", "provision_only"])
def test_a_mapping_to_content_on_an_altered_source_is_not_grounded(client, world, grounding):
    tag = f"gr{next(_n)}"
    over = {"rv_key": "rv_alt", "source_key": "alterable"}
    records = [rec(tag, **over)] if grounding == "requirement_version" else [rec(tag, requirement_version_id=..., **over)]
    did = _create(client, tag, records)
    assert client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()["ok"] is True  # intact: accepted
    with _altered_source():
        rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
        assert rep["ok"] is False and _codes(rep["issues"]) == {"RULE_SOURCE_MAPPING_SOURCE_NOT_GROUNDED"}
        issue = next(i for i in rep["issues"] if i["code"] == "RULE_SOURCE_MAPPING_SOURCE_NOT_GROUNDED")
        assert (issue["source_mapping_id"], issue["source_id"], issue["requirement_version_id"]) == \
            (f"sm_{tag}_root", S["alterable"]["source_id"], S["rv_alt"]["requirement_version_id"])
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
    assert rep["ok"] is True  # restored: grounded again


# ------------------------------------------------------------------------------------------------ every later stage
@pytest.mark.parametrize("problem", ["provision_fingerprint", "altered_source"])
@pytest.mark.parametrize("stage", ["submit-review", "shadow", "publish"])
def test_every_stage_refuses_the_ungrounded_mapping_and_writes_nothing(client, world, stage, problem):
    tag = f"sg{next(_n)}"
    did = _create(client, tag, _provision_only_right_fingerprint(tag) if problem == "provision_fingerprint"
                  else [rec(tag, rv_key="rv_alt", source_key="alterable")])
    _advance(client, did, stage)
    code = "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH" if problem == "provision_fingerprint" \
        else "RULE_SOURCE_MAPPING_SOURCE_NOT_GROUNDED"
    actor = {"submit-review": AUTHOR, "shadow": RELEASE, "publish": RELEASE}[stage]
    body = {"json": {"label": f"smg-{did[:8]}", "changelog": "x"}} if stage == "publish" else {}

    def attempt():
        state, before = _draft_state(did), db_fingerprint()
        r = client.post(f"/api/rules/drafts/{did}/{stage}", headers=H(actor), **body)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        if stage == "publish":
            assert detail["error"] == "RELEASE_CONTENT_NOT_VERIFIED" and code in {p["code"] for p in detail["problems"]}
        else:
            assert detail["error"] == code
        if stage != "submit-review":  # submit-review records VALIDATION_FAILED and its report, as for any error
            assert _draft_state(did) == state and db_fingerprint() == before
    if problem == "provision_fingerprint":
        _replace_mappings(did, _provision_only_wrong_fingerprint(tag))
        attempt()
    else:
        with _altered_source():
            attempt()


# ------------------------------------------------------------------------------------------------ coverage report
def test_the_validation_report_shows_coverage_and_orphans_without_blocking(client, world):
    tag = f"cv{next(_n)}"
    records = [rec(tag), rec(tag, "unused"), rec(tag, "loose", requirement_version_id=None, provision_id=None)]
    did = _create(client, tag, records)
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
    cov = rep["source_mapping_coverage"]
    assert cov["referenced"] == [f"sm_{tag}_root"] and cov["unresolved"] == []
    assert cov["orphaned_records"] == sorted([f"sm_{tag}_unused", f"sm_{tag}_loose"])
    assert cov["ungrounded_records"] == [f"sm_{tag}_loose"]
    assert {f["fact_key"] for f in cov["fact_mappings"]} == {"entity.established_in_eu", "entity.financial_entity_type"}
    # The orphan is not an error; only the ungrounded record is (it was already).
    assert _codes(rep["issues"]) == {"RULE_SOURCE_MAPPING_UNGROUNDED"}
    stored = client.get(f"/api/rules/drafts/{did}", headers=H(AUTHOR)).json()["validation_report"]
    assert stored["source_mapping_coverage"] == cov
    # A draft with only an orphan besides valid content validates.
    tag2 = f"cv{next(_n)}"
    ok = _create(client, tag2, [rec(tag2), rec(tag2, "spare")])
    rep2 = client.post(f"/api/rules/drafts/{ok}/validate", headers=H(AUTHOR)).json()
    assert rep2["ok"] is True and rep2["source_mapping_coverage"]["orphaned_records"] == [f"sm_{tag2}_spare"]


def test_coverage_of_malformed_content_is_reported_without_failing(client, world):
    tag = f"mf{next(_n)}"
    did = _create(client, tag, [rec(tag), "not-a-record", {"mapping_type": "DIRECT_TEXT"}],
                  ast_=ast(tag, mappings=f"sm_{tag}_root"))  # a string where a list is required
    r = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    cov = r.json()["source_mapping_coverage"]
    assert cov["referenced"] == [] and cov["orphaned_records"] == [f"sm_{tag}_root"]
    assert all(len(x) > 1 for f in cov["fact_mappings"] for x in f["source_mapping_ids"])  # never split into characters
