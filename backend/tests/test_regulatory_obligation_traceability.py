"""Regulatory-to-obligation traceability integrity, end to end.

One chain built in its own instrument and organisations:

  Source -> RequirementVersion -> Provisions -> ObligationDefinition (explicit links) -> signed release + RuleVersion
    -> ApplicabilityEvaluation -> ApplicabilityDetermination ADOPTED + attestation -> ObligationInstance
    -> SatisfactionExpression (control mapping) -> AssuranceSnapshot

and a second definition with its own requirement and rule, used as the "wrong" target of mismatched references.

Checked: the forward trace (source / requirement -> definitions -> rule) and the reverse trace (evaluation / obligation
-> rule -> bound requirements) agree; every link that is broken, mismatched or cannot be resolved is reported
explicitly, never traced as intact and never a failure of a whole register; historical reads list only what was
recorded by their cutoff; another organisation's decision records are never shown; reads write nothing.

Requirements: ZA-DOM-001 §1 canonical chain ("Every link preserves the exact versions used"), §13 cardinality ("each
link is explicit"), DOM-INV-11; ZA-APP-001 §07 adoption stage, APP-REQ-003; ZA-TIM-001 TIM-REQ-005/006/017;
ZA-DAT-001 DAT-REQ-012 (released content immutable). Runs on SQLite and PostgreSQL 17; the race is PostgreSQL-only.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H, add_principal
from test_rule_integrity import _tampered

TAG = secrets.token_hex(3).upper()
INSTR = f"ROT-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
ORGS = {k: (f"tenant-rot{k.lower()}-{TAG.lower()}", f"subj-rot{k.lower()}-{TAG.lower()}", f"rot{k.lower()}-cco-{TAG.lower()}")
        for k in ("A", "B")}
FACTS = (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION"))
S: dict = {}
_labels = iter(range(1, 10_000))


# ------------------------------------------------------------------------------------------ content helpers
def _ast(tag: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}


VECTORS = [
    {"vector_id": "ROT-POS", "vector_class": "POSITIVE", "description": "in scope", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "APPLIES"},
    {"vector_id": "ROT-NEG", "vector_class": "NEGATIVE", "description": "outside the EU", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "ROT-UNK", "vector_class": "UNKNOWN", "description": "type unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"}]


def _requirement(client, key: str, valid_from: str, suffix: str = "") -> dict:
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
        "title": f"Traceability requirement {key}{suffix}", "locator": "Art. 7", "valid_from": valid_from,
        "provisions": [
            {"locator": "Art. 7(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "outsourcing register", "normalized_meaning": f"Keep the register{suffix}."},
            {"locator": "Art. 7(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "review register",
             "regulated_object": "outsourcing register", "normalized_meaning": "Review it annually."}]})
    assert r.status_code == 201, r.text
    rv = r.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return rv


def _definition(key: str, provision_ids: list[str]) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=key, title=f"Traceability obligation {key}", instrument_id=inst.instrument_id,
                                 provision_ids=provision_ids, subject_type="LEGAL_ENTITY", coverage_mode="ENCODED",
                                 schedule_kind="CONTINUOUS", material=True,
                                 satisfaction_template={"node_type": "CONTROL", "node_id": f"ctl_{key}",
                                                        "control_key": S["cd_key"]})
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _verified_draft(odef_id: str, rv: dict, tag: str, provision: dict) -> str:
    """A draft taken through review and shadow. Every mapping cites `provision`, which the definition links, so the
    release stays verifier-INTACT (mapping membership in the definition's links is an open content decision)."""
    m = lambda sid: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],  # noqa: E731
                     "provision_id": provision["provision_id"], "authoritative_reference": provision["locator"],
                     "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT"}
    d = client_post(f"/api/rules/drafts", AUTHOR, {
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m(f"sm_{tag}_root"), m(f"sm_{tag}_1"), m(f"sm_{tag}_2")]})
    did = d["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        client_post(f"/api/rules/drafts/{did}/{step}", actor)
    return did


def _release(odef_id: str, rv: dict, tag: str, provision: dict) -> dict:
    did = _verified_draft(odef_id, rv, tag, provision)
    label = f"rot-{TAG}-{next(_labels)}"
    return client_post(f"/api/rules/drafts/{did}/publish", RELEASE, {"label": label, "changelog": label})


def client_post(url: str, actor: str, body: dict | None = None) -> dict:
    r = S["client"].post(url, headers=H(actor), **({"json": body} if body is not None else {}))
    assert r.status_code in (200, 201), (url, r.text)
    return r.json()


def _get(url: str, actor: str, **params) -> dict:
    r = S["client"].get(url, headers=H(actor), params=params)
    assert r.status_code == 200, (url, r.status_code, r.text)
    return r.json()


def _watermark() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _codes(integrity: dict) -> set[str]:
    return {p["code"] for p in integrity["problems"]}


# ------------------------------------------------------------------------------------------ the chain
@pytest.fixture(scope="module")
def world(client):
    from app.db import CELL_ID, AdminSession
    from app.models import ControlDefinition, Instrument, LegalEntity, Subject, Tenant
    S["client"] = client
    S["cd_key"] = f"CTRL-{TAG}-REGISTER"
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Traceability Instrument", jurisdiction="EU",
                          authority="Regulatory Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=S["cd_key"], title="Keep the outsourcing register", objective="Register kept.",
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

    S["source"] = client_post("/api/catalog/sources", AUTHOR, {
        "authority": "Regulatory Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Traceability Regulation", "locator": f"OJ L {TAG}/rot", "instrument_identifier": INSTR,
        "raw_text": "Financial entities shall keep an outsourcing register and shall review it annually."})
    S["rv"] = _requirement(client, f"ROT-{TAG}-MAIN", "2025-01-01")
    S["rv_other"] = _requirement(client, f"ROT-{TAG}-OTHER", "2025-01-01")
    S["odef"] = _definition(f"ROT_{TAG}_MAIN", [p["provision_id"] for p in S["rv"]["provisions"]])
    S["odef_other"] = _definition(f"ROT_{TAG}_OTHER", [p["provision_id"] for p in S["rv_other"]["provisions"]])
    S["rule"] = _release(S["odef"], S["rv"], "m", S["rv"]["provisions"][0])["rule_version"]
    S["rule_other"] = _release(S["odef_other"], S["rv_other"], "o", S["rv_other"]["provisions"][0])["rule_version"]

    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for org in ("A", "B"):
        tid, sid, cco = ORGS[org]
        for key, value in FACTS:
            client_post(f"/api/subjects/{sid}/facts", cco, {"fact_key": key, "value": value, "valid_from": at})
        S[f"E_{org}"] = client_post("/api/applicability/evaluations", cco,
                                    {"subject_id": sid, "obligation_definition_id": S["odef"]})
        assert S[f"E_{org}"]["outcome"] == "APPLIES"
    # Organisation A adopts; organisation B's determination stays PROPOSED.
    adopt = client_post(f"/api/applicability/determinations/{S['E_A']['current_determination']['lineage_id']}/attest",
                        ORGS["A"][2], {"decision": "ADOPT", "authority_basis": "Board resolution ROT-1"})
    S["adopt"], S["lineage"] = adopt, adopt["obligation"]["lineage_id"]
    S["seq_adopted"] = adopt["obligation"]["record_from_seq"]
    e_at = datetime.fromisoformat(S["E_A"]["effective_at"].replace("Z", "+00:00"))
    S["period"] = {"period_from": e_at.isoformat(), "period_to": (e_at + timedelta(hours=1)).isoformat()}
    return True


# ------------------------------------------------------------------------------------------ complete chain
def test_the_complete_chain_reads_the_same_forward_and_reverse(world):
    """Source and requirement (forward) and evaluation, obligation and definition (reverse) name the same definition,
    rule, requirement version and provisions; every verdict is INTACT and no signature trust is claimed."""
    cco = ORGS["A"][2]
    rv_id, rule_id = S["rv"]["requirement_version_id"], S["rule"]["rule_version_id"]
    pids = sorted(p["provision_id"] for p in S["rv"]["provisions"])

    src = _get(f"/api/catalog/sources/{S['source']['source_id']}/trace", AUTHOR)
    fwd_req = next(r for r in src["requirements"] if r["requirement"]["requirement_version_id"] == rv_id)
    for downstream in (fwd_req["obligation_definitions"],
                       _get(f"/api/catalog/requirements/{rv_id}/trace", AUTHOR)["obligation_definitions"]):
        (d,) = [x for x in downstream if x["obligation_definition_id"] == S["odef"]]
        assert d["provision_ids"] == pids
        cur = d["current_rule_version"]
        assert (cur["rule_version_id"], cur["binding_basis"], cur["release_binds"]) == (rule_id, "RELEASE_BOUND", [rv_id])
        assert cur["links_match_release"] is True

    odef = _get(f"/api/catalog/obligation-definitions/{S['odef']}/trace", AUTHOR)
    assert [r["requirement"]["requirement_version_id"] for r in odef["requirements"]] == [rv_id]
    assert odef["current_rule"]["rule_version_id"] == rule_id
    assert odef["current_rule"]["integrity"]["status"] == "INTACT", odef["current_rule"]["integrity"]

    ev = _get(f"/api/applicability/evaluations/{S['E_A']['evaluation_id']}", cco)
    dossier = _get(f"/api/obligations/{S['lineage']}", cco)
    for prov in (ev["regulatory_provenance"], dossier["regulatory_provenance"], odef["current_rule"]):
        assert prov["rule_version_id"] == rule_id and prov["binding_basis"] == "RELEASE_BOUND"
        (req,) = prov["requirements"]
        assert req["requirement"]["requirement_version_id"] == rv_id
        assert sorted(p["provision_id"] for p in req["provisions"]) == pids
        assert prov["integrity"]["status"] == "INTACT" and prov["integrity"]["signature_verified"] is False
    assert dossier["regulatory_provenance"]["evaluation"]["evaluation_id"] == S["E_A"]["evaluation_id"]
    assert dossier["decision_history"]["integrity"]["status"] == "INTACT"
    assert [p["provision_id"] for p in dossier["provisions"]] == [p["provision_id"] for p in S["rv"]["provisions"]]
    assert dossier["expression_versions"][0]["control_mappings"][0]["control_key"] == S["cd_key"]


# ------------------------------------------------------------------------------------------ mismatched references
def test_an_evaluation_that_names_another_definitions_rule_is_a_mismatch(world):
    """Pre-fix: traced as INTACT, so the other definition's legal basis was shown as this decision's."""
    cco, eid = ORGS["A"][2], S["E_A"]["evaluation_id"]
    with _tampered("applicability_evaluation", "evaluation_id", eid, rule_version_id=S["rule_other"]["rule_version_id"]):
        prov = _get(f"/api/applicability/evaluations/{eid}", cco)["regulatory_provenance"]
        assert prov["integrity"]["status"] == "MISMATCH"
        (p,) = [p for p in prov["integrity"]["problems"] if p["code"] == "RULE_DEFINITION_MISMATCH"]
        assert (p["obligation_definition_id"], p["rule_obligation_definition_id"]) == (S["odef"], S["odef_other"])
        listed = next(e for e in _get("/api/applicability/evaluations", cco, subject_id=ORGS["A"][1])
                      if e["evaluation_id"] == eid)
        assert "RULE_DEFINITION_MISMATCH" in _codes(listed["regulatory_provenance"]["integrity"])
        dossier = _get(f"/api/obligations/{S['lineage']}", cco)
        assert "RULE_DEFINITION_MISMATCH" in _codes(dossier["regulatory_provenance"]["integrity"])
        assert any(p["code"] == "RULE_DEFINITION_MISMATCH" and p["role"] == "origin"
                   for p in dossier["decision_history"]["integrity"]["problems"])
    # The shared trace of the other rule is not marked by one evaluation's defect.
    assert _get(f"/api/catalog/obligation-definitions/{S['odef_other']}/trace", AUTHOR)["current_rule"]["integrity"]["status"] == "INTACT"
    assert _get(f"/api/applicability/evaluations/{eid}", cco)["regulatory_provenance"]["integrity"]["status"] == "INTACT"


def test_definition_links_changed_after_release_are_reported_forward_and_reverse(world):
    """A released definition's links are released content: a link added below the application no longer matches what
    its release bound. Pre-fix: the reverse trace stayed INTACT while the forward trace already showed the new link."""
    cco = ORGS["A"][2]
    linked = [p["provision_id"] for p in S["rv"]["provisions"]]
    foreign = S["rv_other"]["provisions"][1]["provision_id"]
    with _tampered("catalog_obligation_definition", "obligation_definition_id", S["odef"], provision_ids=[*linked, foreign]):
        cur = _get(f"/api/catalog/obligation-definitions/{S['odef']}/trace", AUTHOR)["current_rule"]
        (p,) = [p for p in cur["integrity"]["problems"] if p["code"] == "DEFINITION_LINKS_DIFFER_FROM_RELEASE"]
        assert (p["linked_not_bound"], p["bound_not_linked"], p["class"]) == ([foreign], [], "MISMATCH")
        fwd = _get(f"/api/catalog/requirements/{S['rv_other']['requirement_version_id']}/trace", AUTHOR)
        by_def = {d["obligation_definition_id"]: d["current_rule_version"] for d in fwd["obligation_definitions"]}
        assert by_def[S["odef"]]["links_match_release"] is False and by_def[S["odef_other"]]["links_match_release"] is True
        issues = _get("/api/catalog/traceability/validation", AUTHOR)["issues"]
        assert any(i["code"] == "RELEASE_BINDING_DEFINITION_MISMATCH" and i["rule_version_id"] == S["rule"]["rule_version_id"]
                   and i["linked_not_bound"] == [foreign] for i in issues)
        prov = _get(f"/api/applicability/evaluations/{S['E_A']['evaluation_id']}", cco)["regulatory_provenance"]
        assert "DEFINITION_LINKS_DIFFER_FROM_RELEASE" in _codes(prov["integrity"])
    assert _get(f"/api/catalog/obligation-definitions/{S['odef']}/trace", AUTHOR)["current_rule"]["integrity"]["status"] == "INTACT"


# ------------------------------------------------------------------------------------------ orphaned and missing links
def test_an_obligation_whose_definition_cannot_be_found_is_reported_not_a_failure(world):
    """Tenant records name catalogue definitions by ID (no foreign key). Pre-fix: one such obligation made the whole
    obligation register and its dossier fail (500)."""
    cco, rev = ORGS["A"][2], S["adopt"]["obligation"]["revision_id"]
    with _tampered("assurance_obligation_instance_revision", "revision_id", rev, obligation_definition_id="definition-gone"):
        (o,) = [o for o in _get("/api/obligations", cco) if o["lineage_id"] == S["lineage"]]
        assert o["definition"] == {"obligation_definition_id": "definition-gone", "key": None, "title": None,
                                   "schedule_kind": None, "missing": True}
        d = _get(f"/api/obligations/{S['lineage']}", cco)
        assert d["definition"]["missing"] is True and d["provisions"] == []
        assert "OBLIGATION_DEFINITION_MISSING" in _codes(d["regulatory_provenance"]["integrity"])
        assert d["regulatory_provenance"]["integrity"]["status"] in ("INCOMPLETE", "MISMATCH")
        assert "DETERMINATION_MISMATCH" in _codes(d["decision_history"]["integrity"])


def test_decisions_and_evaluations_whose_definition_cannot_be_found_are_reported(world):
    cco = ORGS["A"][2]
    det_rev = S["adopt"]["determination"]["revision_id"]
    with _tampered("applicability_determination_revision", "revision_id", det_rev, obligation_definition_id="definition-gone"):
        (row,) = [d for d in _get("/api/applicability/determinations", cco) if d["revision_id"] == det_rev]
        assert row["obligation_definition"] is None and row["missing_references"] == ["obligation_definition_id"]
    eid = S["E_A"]["evaluation_id"]
    with _tampered("applicability_evaluation", "evaluation_id", eid, obligation_definition_id="definition-gone"):
        e = _get(f"/api/applicability/evaluations/{eid}", cco)
        assert e["obligation_definition"] == {"obligation_definition_id": "definition-gone", "key": None, "title": None,
                                              "missing": True}
        assert e["replay"]["status"] == "INCOMPLETE"
        assert "RULE_DEFINITION_MISMATCH" in _codes(e["regulatory_provenance"]["integrity"])
        listed = [x for x in _get("/api/applicability/evaluations", cco, subject_id=ORGS["A"][1]) if x["evaluation_id"] == eid]
        assert listed[0]["obligation_definition"]["missing"] is True


def test_an_unresolvable_determination_is_reported_explicitly_in_the_dossier(world):
    """Pre-fix: the dossier's regulatory provenance was simply null."""
    cco, rev = ORGS["A"][2], S["adopt"]["obligation"]["revision_id"]
    with _tampered("assurance_obligation_instance_revision", "revision_id", rev, determination_revision_id="determination-gone"):
        prov = _get(f"/api/obligations/{S['lineage']}", cco)["regulatory_provenance"]
    assert prov["binding_basis"] == "INCOMPLETE" and prov["missing"] is True
    assert prov["integrity"]["status"] == "INCOMPLETE"
    assert [p for p in prov["integrity"]["problems"] if p["code"] == "DETERMINATION_MISSING"] == [
        {"code": "DETERMINATION_MISSING", "class": "INCOMPLETE", "determination_revision_id": "determination-gone"}]


# ------------------------------------------------------------------------------------------ tenant isolation
def test_another_organisations_decision_records_are_never_shown(world):
    """An obligation that names another organisation's determination: reported, with none of that record's content.
    (On PostgreSQL row-level security already hides the row; pre-fix, SQLite returned it in the dossier.)"""
    cco = ORGS["A"][2]
    rev = S["adopt"]["obligation"]["revision_id"]
    b_det = S["E_B"]["current_determination"]
    with _tampered("assurance_obligation_instance_revision", "revision_id", rev, determination_revision_id=b_det["revision_id"]):
        d = _get(f"/api/obligations/{S['lineage']}", cco)
    assert d["determination"] is None
    assert "DETERMINATION_MISSING" in _codes(d["regulatory_provenance"]["integrity"])
    origin = d["decision_history"]["revisions"][0]
    assert origin["origin"]["determination"] is None and origin["origin"]["evaluation"] is None
    body = json.dumps(d)
    assert ORGS["B"][1] not in body and S["E_B"]["evaluation_id"] not in body and ORGS["B"][0] not in body


def test_another_organisations_records_do_not_leak_into_the_obligation_register(world):
    """Exceptions and snapshots are counted for the caller's own organisation only, by the query itself."""
    from app.db import AdminSession
    from app.models import ExceptionRecord
    cco = ORGS["A"][2]

    def mine():
        return next(o for o in _get("/api/obligations", cco) if o["lineage_id"] == S["lineage"])["open_exceptions"]
    before = mine()
    with AdminSession() as db:
        x = ExceptionRecord(tenant_id=ORGS["B"][0], target_type="OBLIGATION", target_id=S["lineage"], state="APPROVED",
                            reason="Another organisation's record", requested_by=ORGS["B"][2],
                            valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
                            valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc), record_seq=_watermark())
        db.add(x)
        db.commit()
        xid = x.exception_id
    try:
        assert mine() == before
        assert S["client"].get(f"/api/obligations/{S['lineage']}", headers=H(ORGS["B"][2])).status_code == 404
        assert S["client"].get(f"/api/applicability/evaluations/{S['E_A']['evaluation_id']}",
                               headers=H(ORGS["B"][2])).status_code == 404
    finally:
        # The table is append-only (a database trigger): the probe row is removed as _tampered restores rows, with the
        # owner's triggers disabled inside its own transaction.
        from app.db import IS_POSTGRES, admin_engine
        with admin_engine.begin() as c:
            if IS_POSTGRES:
                c.execute(text("ALTER TABLE assurance_exception_revision DISABLE TRIGGER USER"))
            c.execute(text("DELETE FROM assurance_exception_revision WHERE exception_id = :x"), {"x": xid})
            if IS_POSTGRES:
                c.execute(text("ALTER TABLE assurance_exception_revision ENABLE TRIGGER USER"))


# ------------------------------------------------------------------------------------------ historical reads
def test_a_historical_dossier_lists_only_snapshots_recorded_by_its_cutoff(world):
    """A snapshot computed later for an earlier cutoff (AS_KNOWN_THEN) was not known at that cutoff. Pre-fix: listed,
    because only its record_cutoff_seq was compared."""
    from app.db import AdminSession
    from app.models import OutboxEvent
    cco = ORGS["A"][2]
    snap = client_post(f"/api/obligations/{S['lineage']}/snapshots", cco,
                       {**S["period"], "record_cutoff_seq": S["seq_adopted"]})
    sid = snap["snapshot_id"]
    with AdminSession() as db:
        created = db.execute(select(OutboxEvent.record_seq).where(OutboxEvent.aggregate_id == sid,
                                                                  OutboxEvent.event_type == "assurance.snapshot_created")).scalar_one()
    assert snap["record_cutoff_seq"] == S["seq_adopted"] < created

    def listed(**params):
        return sid in {s["snapshot_id"] for s in _get(f"/api/obligations/{S['lineage']}", cco, **params)["snapshots"]}
    assert not listed(as_of_record_seq=S["seq_adopted"])
    assert not listed(as_of_record_seq=created - 1)
    assert listed(as_of_record_seq=created) and listed()


def test_a_superseded_requirement_keeps_its_historical_relationships(world):
    """A corrected requirement version released for another definition supersedes the bound one: the decision still
    traces to the version it was made on, intact, and the forward traces keep each version with its own definition."""
    cco = ORGS["A"][2]
    rv2 = _requirement(S["client"], S["rv"]["requirement_key"], "2026-01-01", " (corrected)")
    odef_v2 = _definition(f"ROT_{TAG}_V2", [p["provision_id"] for p in rv2["provisions"]])
    _release(odef_v2, rv2, "v", rv2["provisions"][0])
    old_fwd = _get(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/trace", AUTHOR)
    assert old_fwd["requirement"]["lifecycle"] == "SUPERSEDED"
    assert [d["obligation_definition_id"] for d in old_fwd["obligation_definitions"]] == [S["odef"]]
    new_fwd = _get(f"/api/catalog/requirements/{rv2['requirement_version_id']}/trace", AUTHOR)
    assert [d["obligation_definition_id"] for d in new_fwd["obligation_definitions"]] == [odef_v2]
    prov = _get(f"/api/applicability/evaluations/{S['E_A']['evaluation_id']}", cco)["regulatory_provenance"]
    assert prov["requirements"][0]["requirement"]["requirement_version_id"] == S["rv"]["requirement_version_id"]
    assert prov["requirements"][0]["content_matches_binding"] is True and prov["integrity"]["status"] == "INTACT"
    hist = _get(f"/api/obligations/{S['lineage']}", cco, as_of_record_seq=S["seq_adopted"])
    assert hist["regulatory_provenance"]["requirements"][0]["requirement"]["requirement_version_id"] == S["rv"]["requirement_version_id"]


def test_history_compare_refuses_unknown_definitions_and_unreached_cutoffs(world):
    """Pre-fix: an unknown definition was a 500, and a cutoff not reached yet was answered with today's records."""
    cco, sid = ORGS["A"][2], ORGS["A"][1]
    now = _watermark()
    base = {"subject_id": sid, "obligation_definition_id": S["odef"], "valid_at": S["E_A"]["effective_at"]}
    c = S["client"]
    r = c.get("/api/history/compare", headers=H(cco), params={**base, "obligation_definition_id": "gone", "left_seq": 1,
                                                                "right_seq": 1})
    assert r.status_code == 404
    r = c.get("/api/history/compare", headers=H(cco), params={**base, "left_seq": 1, "right_seq": now + 1000})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE"
    assert c.get("/api/history/compare", headers=H(cco), params={**base, "left_seq": -1, "right_seq": 1}).status_code == 422
    ok = _get("/api/history/compare", cco, **base, left_seq=S["seq_adopted"], right_seq=now)
    assert ok["left"]["known_seq"] == S["seq_adopted"] and ok["right"]["known_seq"] == now


# ------------------------------------------------------------------------------------------ read-only
def test_traceability_reads_write_nothing_and_answer_the_same_twice(world):
    from app.db import admin_engine
    cco = ORGS["A"][2]
    tables = ["platform_outbox_event", "platform_audit_event", "applicability_evaluation",
              "applicability_determination_revision", "assurance_obligation_instance_revision", "assurance_snapshot",
              "governance_attestation", "catalog_rule_version", "catalog_content_release", "catalog_requirement_version",
              "catalog_provision", "catalog_obligation_definition", "applicability_fact_request"]

    def state():
        with admin_engine.connect() as conn:
            return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}, _watermark()

    def traces():
        rv = S["rv"]["requirement_version_id"]
        return [_get(f"/api/catalog/sources/{S['source']['source_id']}/trace", AUTHOR),
                _get(f"/api/catalog/requirements/{rv}/trace", AUTHOR),
                _get(f"/api/catalog/obligation-definitions/{S['odef']}/trace", AUTHOR),
                _get(f"/api/catalog/obligation-definitions/{S['odef']}", AUTHOR),
                _get(f"/api/applicability/evaluations/{S['E_A']['evaluation_id']}", cco)["regulatory_provenance"],
                _get(f"/api/obligations/{S['lineage']}", cco, as_of_record_seq=S["seq_adopted"])["regulatory_provenance"]]
    before = state()
    first = traces()
    assert traces() == first
    _get("/api/catalog/traceability/validation", AUTHOR)
    _get("/api/obligations", cco)
    _get("/api/applicability/determinations", cco)
    _get("/api/applicability/evaluations", cco, subject_id=ORGS["A"][1])
    _get("/api/history/compare", cco, subject_id=ORGS["A"][1], obligation_definition_id=S["odef"],
         valid_at=S["E_A"]["effective_at"], left_seq=S["seq_adopted"], right_seq=S["seq_adopted"])
    assert state() == before


# ------------------------------------------------------------------------------------------ concurrency (PostgreSQL)
def test_a_link_edit_racing_a_publication_cannot_split_links_from_the_release(world, monkeypatch):
    """The publication holds the definition row: a link edit waits, then sees the release and is refused (409), so the
    released links and the binding agree and both traces stay consistent."""
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.engines import catalog
    from app.models import Actor
    from app.routers.rules import PublishIn, publish as publish_route
    from app.security import Ctx
    rv = _requirement(S["client"], f"ROT-{TAG}-RACE", "2025-01-01")
    p0, p1 = rv["provisions"]
    odef = _definition(f"ROT_{TAG}_RACE", [p0["provision_id"]])
    did = _verified_draft(odef, rv, "r", p0)
    original, reached, go = appl.lock_rule_scope, threading.Event(), threading.Event()

    def held(db, odef_id, *a, **k):
        if odef_id == odef:
            reached.set()
            assert go.wait(30)
        return original(db, odef_id, *a, **k)
    monkeypatch.setattr(appl, "lock_rule_scope", held)
    out: dict = {}

    def run(actor, fn, key):
        with SessionLocal() as db:
            set_principal(db, None, actor)
            db.execute(text("SET lock_timeout = '30s'"))
            try:
                out[key] = ("ok", fn(Ctx(db=db, actor=db.get(Actor, actor))))
                db.commit()
            except HTTPException as e:
                db.rollback()
                out[key] = (e.status_code, e.detail)
    pub = threading.Thread(target=run, args=(RELEASE, lambda ctx: publish_route(
        did, PublishIn(label=f"rot-{TAG}-race", changelog="race"), ctx), "publish"))
    pub.start()
    assert reached.wait(30)
    link = threading.Thread(target=run, args=(AUTHOR, lambda ctx: catalog.add_link(
        ctx, odef, rv["requirement_version_id"], p1["provision_id"], "race"), "link"))
    link.start()
    link.join(1.5)
    assert link.is_alive() and "link" not in out, "the link edit must wait for the publication holding the definition"
    go.set()
    pub.join(30)
    link.join(30)
    assert out["publish"][0] == "ok", out
    assert out["link"][0] == 409 and out["link"][1]["error"] == "OBLIGATION_DEFINITION_RELEASED", out
    cur = _get(f"/api/catalog/obligation-definitions/{odef}/trace", AUTHOR)["current_rule"]
    assert cur["integrity"]["status"] == "INTACT", cur["integrity"]
    fwd = _get(f"/api/catalog/requirements/{rv['requirement_version_id']}/trace", AUTHOR)["obligation_definitions"]
    assert [(d["provision_ids"], d["current_rule_version"]["links_match_release"]) for d in fwd] == [([p0["provision_id"]], True)]
