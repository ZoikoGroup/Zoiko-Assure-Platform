"""Release-bound source mappings (ZA-RUL-001 §12, RUL-REQ-008/027).

§12: "Reviewer notes may be outside executable hash only if separately integrity-protected in release manifest; source
mappings required for legal interpretation remain release-bound." "Whitespace/order: Never legally meaningful."

The AST hash is unchanged (mappings are not executable). Each signed manifest rule-version entry binds the exact records
stored on the immutable RuleVersion: `source_mappings_profile`, `source_mappings` ([{source_mapping_id, sha256}] sorted by
ID) and `source_mappings_sha256`. Verification (GET /catalog/releases/{id}/verify, check SOURCE_MAPPING_BINDING), the
evaluator's integrity check before input freeze (SOURCE_MAPPINGS_MISMATCH, fail closed) and the historical trace detect
any missing, added, altered or substituted record; a different storage order is not a change. Releases that predate the
binding are reported as unverifiable, never as altered.
"""
from __future__ import annotations

import json
import secrets

import pytest
from sqlalchemy import select, text

from conftest import H
from test_authorization import db_fingerprint
from test_rule_integrity import _tampered
from test_source_mapping_inheritance import VECTORS, pred

TAG = secrets.token_hex(3).upper()
INSTR = f"SRB-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
S: dict = {}
_n = iter(range(10**6))


# ------------------------------------------------------------------------------------------------ pure binding
def _rec(mid, **over):
    m = {"source_mapping_id": mid, "requirement_version_id": "rv-1", "provision_id": "p-1",
         "authoritative_reference": "Art. 2(1)", "source_fingerprint": "a" * 64, "mapping_type": "DIRECT_TEXT",
         "reviewer": LEGAL, "review_date": "2026-11-01", "scope_note": "EU only", "rights_class": "PUBLIC"}
    return {**m, **over}


def test_binding_is_deterministic_and_order_independent():
    from app.engines.catalog import SOURCE_MAPPINGS_PROFILE, source_mapping_binding
    a, b = _rec("sm_a"), _rec("sm_b", provision_id="p-2")
    one = source_mapping_binding([a, b])
    assert one == source_mapping_binding([dict(reversed(list(b.items()))), a])  # record order and key order are not content
    assert one["source_mappings_profile"] == SOURCE_MAPPINGS_PROFILE
    assert [e["source_mapping_id"] for e in one["source_mappings"]] == ["sm_a", "sm_b"]
    assert len(one["source_mappings_sha256"]) == 64 and all(len(e["sha256"]) == 64 for e in one["source_mappings"])
    assert source_mapping_binding([]) == source_mapping_binding(None)


@pytest.mark.parametrize("field, value", [
    ("mapping_type", "INTERPRETIVE"), ("interpretation_rationale", "Why."), ("authoritative_reference", "Art. 2(2)"),
    ("source_fingerprint", "b" * 64), ("requirement_version_id", "rv-2"), ("provision_id", "p-9"),
    ("source_mapping_id", "sm_z"), ("reviewer", "someone-else"), ("review_date", "2026-11-02"),
    ("scope_note", "UK only"), ("rights_class", "LICENSED")])
def test_every_stored_field_is_bound(field, value):
    """§08 fields and reviewer notes alike: §12 lets reviewer notes sit outside the executable hash only if
    integrity-protected in the manifest."""
    from app.engines.catalog import source_mapping_binding
    base = [_rec("sm_a"), _rec("sm_b")]
    changed = [_rec("sm_a", **{field: value}), _rec("sm_b")]
    assert source_mapping_binding(base)["source_mappings_sha256"] != source_mapping_binding(changed)["source_mappings_sha256"]


@pytest.mark.parametrize("case", ["intact", "reordered", "missing", "added", "altered", "substituted", "malformed",
                                  "unknown_profile", "not_bound"])
def test_binding_check_names_each_kind_of_difference(case):
    from app.engines.catalog import source_mapping_binding, source_mapping_binding_check
    a, b = _rec("sm_a"), _rec("sm_b")
    entry = {"rule_version_id": "rv", **source_mapping_binding([a, b])}
    stored = {"intact": [a, b], "reordered": [b, a], "missing": [a], "added": [a, b, _rec("sm_c")],
              "altered": [a, _rec("sm_b", authoritative_reference="Art. 9")],
              "substituted": [a, _rec("sm_b", requirement_version_id="rv-other", provision_id="p-other")],
              "malformed": [a, b], "unknown_profile": [a, b], "not_bound": [a, b]}[case]
    if case == "malformed":
        entry["source_mappings"] = "tampered"
    elif case == "unknown_profile":
        entry["source_mappings_profile"] = "za-source-mappings-v9"
    elif case == "not_bound":
        entry = {"rule_version_id": "rv"}
    res = source_mapping_binding_check(entry, stored)
    expected = {"intact": ("MATCH", [], [], []), "reordered": ("MATCH", [], [], []),
                "missing": ("MISMATCH", ["sm_b"], [], []), "added": ("MISMATCH", [], ["sm_c"], []),
                "altered": ("MISMATCH", [], [], ["sm_b"]), "substituted": ("MISMATCH", [], [], ["sm_b"]),
                "malformed": ("MISMATCH", [], [], [])}
    if case in expected:
        status, missing, unbound, altered = expected[case]
        assert (res["status"], res["missing"], res["unbound"], res["altered"]) == (status, missing, unbound, altered)
    else:
        assert res["status"] == {"unknown_profile": "UNKNOWN_PROFILE", "not_bound": "NOT_BOUND"}[case]


# ------------------------------------------------------------------------------------------------ world + API flow
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Release Binding Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Binding Act",
        "locator": f"OJ L {TAG}/srb", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"SRB-{TAG}-REQ",
        "title": "Register", "locator": "Art. 2", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"},
            {"locator": "Art. 2(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "update register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Update the register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SRB_{TAG}", title="Release binding obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    S["first"] = _release(client, "first")
    return True


def rec(tag, mid, provision=0, **over):
    p = S["rv"]["provisions"][provision]
    return {"source_mapping_id": f"sm_{tag}_{mid}", "requirement_version_id": S["rv"]["requirement_version_id"],
            "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
            "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL, **over}


def _release(client, tag, *, change_class="EXECUTABLE_OUTCOME", records=None, correction_reason=None):
    """Author, review, shadow and publish a rule whose root and two predicates carry their own mappings."""
    ast = {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_a", mapping=[f"sm_{tag}_a"]), pred(f"{tag}_b", mapping=[f"sm_{tag}_b"], fact="entity.financial_entity_type")]}
    records = records if records is not None else [rec(tag, "root"), rec(tag, "a"), rec(tag, "b", provision=1)]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast, "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": change_class, "source_mappings": records})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    body = {"label": f"srb-{tag}-{TAG}", "changelog": tag}
    if correction_reason:
        body["correction_reason"] = correction_reason
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json=body)
    assert r.status_code == 200, r.text
    return {**r.json(), "draft_id": did, "records": records}


def _verify(client, cr_id, actor=AUTHOR) -> dict:
    r = client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()


def _check(report, name):
    return [c for c in report["checks"] if c["check"] == name]


def _row_digests(*keys) -> dict:
    from app.db import admin_engine
    from app.platform import digest
    out = {}
    with admin_engine.connect() as c:
        for t, k, v in keys:
            r = c.execute(text(f"SELECT * FROM {t} WHERE {k} = :v"), {"v": v}).mappings().one()
            out[(t, v)] = digest({x: str(y) for x, y in r.items()})
    return out


def _release_rows(rel) -> tuple:
    return (("catalog_rule_version", "rule_version_id", rel["rule_version"]["rule_version_id"]),
            ("catalog_content_release", "content_release_id", rel["content_release"]["content_release_id"]))


# ------------------------------------------------------------------------------------------------ manifest + signature
def test_release_manifest_binds_the_stored_source_mappings(client, world):
    from app.db import AdminSession
    from app.engines.catalog import SOURCE_MAPPINGS_PROFILE, source_mapping_binding
    from app.models import RuleVersion
    rel = S["first"]
    [entry] = rel["content_release"]["manifest"]["rule_versions"]
    with AdminSession() as db:
        stored = db.get(RuleVersion, rel["rule_version"]["rule_version_id"]).source_mappings
    assert stored == rel["records"]  # the exact records the release was built from, kept on the immutable RuleVersion
    assert entry["source_mappings_profile"] == SOURCE_MAPPINGS_PROFILE
    assert {k: entry[k] for k in ("source_mappings_profile", "source_mappings", "source_mappings_sha256")} == \
        source_mapping_binding(stored)
    assert [e["source_mapping_id"] for e in entry["source_mappings"]] == sorted(m["source_mapping_id"] for m in stored)
    assert entry["ast_sha256"] == rel["rule_version"]["ast_hash"]  # the AST hash contract is unchanged
    report = _verify(client, rel["content_release"]["content_release_id"])
    assert report["integrity"] == "INTACT" and report["manifest_hash_ok"] and report["signature_ok"]
    assert [c["status"] for c in _check(report, "SOURCE_MAPPING_BINDING")] == ["PASS"]
    assert [c["status"] for c in _check(report, "SIGNATURE")] == ["PASS"]  # the signature covers the binding
    assert report["outcome"] == "UNVERIFIABLE"  # signer trust is still undecided (K1), never VALID


@pytest.mark.parametrize("change", ["binding_digest", "binding_entry", "binding_removed"])
def test_changing_the_signed_binding_breaks_the_manifest_hash_and_signature(client, world, change):
    rel = S["first"]
    cr_id = rel["content_release"]["content_release_id"]

    def edited(row):
        m = json.loads(json.dumps(row["manifest"])) if not isinstance(row["manifest"], str) else json.loads(row["manifest"])
        e = m["rule_versions"][0]
        if change == "binding_digest":
            e["source_mappings_sha256"] = "0" * 64
        elif change == "binding_entry":
            e["source_mappings"][0]["sha256"] = "0" * 64
        else:
            for k in ("source_mappings_profile", "source_mappings", "source_mappings_sha256"):
                e.pop(k)
        return m
    with _tampered("catalog_content_release", "content_release_id", cr_id, manifest=edited):
        report = _verify(client, cr_id)
        assert [c["status"] for c in _check(report, "MANIFEST_HASH")] == ["FAIL"]
        assert [c["status"] for c in _check(report, "SIGNATURE")] == ["FAIL"]
        assert report["integrity"] == "ALTERED" and report["outcome"] == "INVALID"
    assert _verify(client, cr_id)["integrity"] == "INTACT"


# ------------------------------------------------------------------------------------------------ stored-record tampering
TAMPER = {
    "altered_field": (lambda recs: [recs[0], {**recs[1], "authoritative_reference": "Art. 99"}, recs[2]], ([], [], "a")),
    "substituted_record": (lambda recs: [recs[0], {**recs[1], "provision_id": S["rv"]["provisions"][1]["provision_id"]}, recs[2]],
                           ([], [], "a")),
    "reviewer_note_changed": (lambda recs: [recs[0], recs[1], {**recs[2], "reviewer": "someone-else"}], ([], [], "b")),
    "record_missing": (lambda recs: recs[:2], ("b", [], [])),
    "record_added": (lambda recs: recs + [{**recs[0], "source_mapping_id": "sm_injected"}], ([], "sm_injected", [])),
}


@pytest.mark.parametrize("kind", sorted(TAMPER))
def test_stored_mapping_tampering_is_detected_everywhere(client, world, kind):
    """A changed record on the RuleVersion is reported by release verification (naming the mapping), refused by the
    evaluator's integrity check (fail closed, before input freeze) and reported by the historical trace."""
    from app.db import AdminSession
    from app.engines import applicability as appl, catalog
    from app.models import RuleVersion
    rel = S["first"]
    rv_id, cr_id = rel["rule_version"]["rule_version_id"], rel["content_release"]["content_release_id"]
    make, (missing, unbound, altered) = TAMPER[kind]
    tag = "first"
    sm = lambda x: [f"sm_{tag}_{x}"] if x else []
    with _tampered("catalog_rule_version", "rule_version_id", rv_id, source_mappings=lambda row: make(list(rel["records"]))):
        report = _verify(client, cr_id)
        [binding] = _check(report, "SOURCE_MAPPING_BINDING")
        assert binding["status"] == "FAIL", binding
        assert (binding["missing"], binding["unbound"], binding["altered"]) == \
            (sm(missing), [unbound] if unbound else [], sm(altered))
        [integrity] = _check(report, "RULE_VERSION_INTEGRITY")
        assert integrity["status"] == "FAIL" and "SOURCE_MAPPINGS_MISMATCH" in integrity["message"]
        assert report["integrity"] == "ALTERED" and report["outcome"] == "INVALID"
        with AdminSession() as db:
            rv = db.get(RuleVersion, rv_id)
            assert appl._integrity_failure(db, rv) == ("SOURCE_MAPPINGS_MISMATCH", rv_id)
            with pytest.raises(appl.RuleIntegrityError):
                appl.verify_rule_integrity(db, rv)
            trace = catalog.trace_rule_version(db, rv)
        assert trace["integrity"]["status"] == "MISMATCH"
        assert {(p["code"], p.get("check")) for p in trace["integrity"]["problems"]} >= {("RULE_INTEGRITY_FAILED", "SOURCE_MAPPINGS_MISMATCH")}
    restored = _verify(client, cr_id)
    assert restored["integrity"] == "INTACT" and [c["status"] for c in _check(restored, "SOURCE_MAPPING_BINDING")] == ["PASS"]


def test_a_different_storage_order_is_not_a_change(client, world):
    """§12: order is never legally meaningful."""
    rel = S["first"]
    rv_id, cr_id = rel["rule_version"]["rule_version_id"], rel["content_release"]["content_release_id"]
    with _tampered("catalog_rule_version", "rule_version_id", rv_id, source_mappings=lambda row: list(reversed(rel["records"]))):
        report = _verify(client, cr_id)
        assert report["integrity"] == "INTACT" and [c["status"] for c in _check(report, "SOURCE_MAPPING_BINDING")] == ["PASS"]


# ------------------------------------------------------------------------------------------------ history and corrections
def test_history_stays_verifiable_after_draft_edits_and_new_releases(client, world):
    """The released records live on the immutable RuleVersion: the published draft is frozen, and later drafts and
    releases with other mappings leave the earlier release, its manifest and its binding exactly as they were."""
    first = S["first"]
    before = _verify(client, first["content_release"]["content_release_id"])
    rows = _row_digests(*_release_rows(first))
    r = client.put(f"/api/rules/drafts/{first['draft_id']}", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": {"node_type": "ALL"}, "valid_from": "2025-01-01",
        "source_mappings": [], "golden_vectors": []})
    assert r.status_code == 409  # a published draft is frozen
    second = _release(client, "second", records=[rec("second", "root", provision=1, mapping_type="CROSS_REFERENCE",
                                                     interpretation_rationale="Points to Art. 2(2)."),
                                                 rec("second", "a"), rec("second", "b")])
    assert second["content_release"]["manifest"]["rule_versions"][0]["source_mappings_sha256"] != \
        first["content_release"]["manifest"]["rule_versions"][0]["source_mappings_sha256"]
    assert _row_digests(*_release_rows(first)) == rows
    after = _verify(client, first["content_release"]["content_release_id"])
    assert after["checks"] == before["checks"] and after["integrity"] == "INTACT"
    S["second"] = second


def test_a_correction_release_binds_its_own_mappings_and_leaves_the_corrected_release_intact(client, world):
    corrected = S["second"]
    before = _verify(client, corrected["content_release"]["content_release_id"])
    rows = _row_digests(*_release_rows(corrected))
    corr = _release(client, "corr", change_class="CORRECTION", correction_reason="Article 2(2) mapping corrected.",
                    records=[rec("corr", "root"), rec("corr", "a"), rec("corr", "b", provision=1,
                                                                        authoritative_reference="Art. 2(2), as corrected")])
    manifest = corr["content_release"]["manifest"]
    assert manifest["correction"]["corrects_release_id"] == corrected["content_release"]["content_release_id"]
    report = _verify(client, corr["content_release"]["content_release_id"])
    assert report["integrity"] == "INTACT"
    assert [c["status"] for c in _check(report, "SOURCE_MAPPING_BINDING")] == ["PASS"]
    assert [c["status"] for c in _check(report, "CORRECTION_METADATA")] == ["PASS"]
    assert _row_digests(*_release_rows(corrected)) == rows
    assert _verify(client, corrected["content_release"]["content_release_id"])["checks"] == before["checks"]


# ------------------------------------------------------------------------------------------------ gate, legacy, access
def test_invalid_source_mappings_cannot_be_released(client, world):
    """A draft whose mapping fingerprint no longer matches (as if changed after review) is refused at publication; no
    release, rule version or binding is written."""
    from app.db import AdminSession
    from app.models import RuleDraft
    tag = f"bad{next(_n)}"
    ast = {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_a"), pred(f"{tag}_b", fact="entity.financial_entity_type")]}
    did = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast, "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": [rec(tag, "root")]}).json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200
    with AdminSession() as db:
        db.get(RuleDraft, did).source_mappings = [rec(tag, "root", source_fingerprint="f" * 64)]
        db.commit()
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"srb-{tag}", "changelog": "x"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RELEASE_CONTENT_NOT_VERIFIED"
    assert "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH" in {p["code"] for p in r.json()["detail"]["problems"]}
    assert db_fingerprint() == before


def test_releases_that_predate_the_binding_are_unverifiable_not_altered(client, world):
    """The seeded catalogue was released before mapping binding: verification and trace say the mappings cannot be
    proven; nothing is reported as altered, and binding evaluation is unaffected."""
    from app.db import AdminSession
    from app.engines import applicability as appl, catalog
    from app.models import ContentRelease, Instrument, ObligationDefinition, RuleVersion
    with AdminSession() as db:
        rv = db.execute(select(RuleVersion).join(ObligationDefinition, ObligationDefinition.obligation_definition_id
                                                 == RuleVersion.obligation_definition_id)
                        .join(Instrument, Instrument.instrument_id == ObligationDefinition.instrument_id)
                        .where(Instrument.identifier == "EU-2022-2554")).scalars().first()
        cr_id = rv.content_release_id
        assert "source_mappings_sha256" not in db.get(ContentRelease, cr_id).manifest["rule_versions"][0]
        assert appl._integrity_failure(db, rv) is None
        trace = catalog.trace_rule_version(db, rv)
    assert "SOURCE_MAPPINGS_NOT_BOUND" in {p["code"] for p in trace["integrity"]["problems"]}
    assert trace["integrity"]["status"] != "MISMATCH"
    report = _verify(client, cr_id)
    assert [c["status"] for c in _check(report, "SOURCE_MAPPING_BINDING")] == ["UNVERIFIABLE"]
    assert not any(c["status"] == "FAIL" and c["check"] == "SOURCE_MAPPING_BINDING" for c in report["checks"])


def test_verification_is_read_only_and_identical_for_every_reader(client, world):
    """Global content: every authorised reader, organisation users included, gets the same report; the catalogue rows
    are not changed by verifying; principals without catalogue read are refused."""
    rel = S["first"]
    cr_id = rel["content_release"]["content_release_id"]
    rows = _row_digests(*_release_rows(rel))
    reports = {a: _verify(client, cr_id, a) for a in ("actor-cco", "actor-ext-auditor", AUTHOR)}
    assert len({json.dumps(r, sort_keys=True) for r in reports.values()}) == 1
    assert "tenant" not in json.dumps(reports[AUTHOR])
    assert client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H("actor-agent")).status_code == 403
    assert _row_digests(*_release_rows(rel)) == rows


def test_the_application_role_cannot_change_released_mappings(client, world):
    """Historical retention rests on the immutable RuleVersion row (migration 0004): the runtime role cannot rewrite
    the released records."""
    from app.db import IS_POSTGRES, SessionLocal
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from sqlalchemy.exc import DBAPIError
    rv_id = S["first"]["rule_version"]["rule_version_id"]
    with SessionLocal() as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE catalog_rule_version SET source_mappings = CAST('[]' AS json) WHERE rule_version_id = :r"),
                       {"r": rv_id})
        db.rollback()
    assert _verify(client, S["first"]["content_release"]["content_release_id"])["integrity"] == "INTACT"
