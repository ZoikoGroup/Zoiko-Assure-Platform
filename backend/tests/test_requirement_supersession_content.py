"""Superseding a released requirement version changes its lifecycle only (CI failure of 2026-11-10, run 42760570438).

test_regulatory_traceability::test_a_new_released_version_supersedes_the_old_one_without_rewriting_it failed on
PostgreSQL 17 in CI with the superseded version's provisions apparently changed. Nothing rewrote them:
GET /catalog/requirements/{id} listed provisions without ORDER BY, so PostgreSQL returned them in physical row order,
and the supersession's lifecycle UPDATE can move a row version to another page (it does in a full-suite database),
which reordered the list. The route now lists provisions by (locator, provision_id), as /history and verify() do, and
publish_bound moves provisions in that order too.

These tests prove, on SQLite and PostgreSQL 17:
  * the listing order does not depend on where PostgreSQL stores the rows (each provision row is relocated by an
    UPDATE that changes nothing, as a lifecycle move would);
  * releasing v2 supersedes v1 and releasing v3 supersedes v2, and every earlier version keeps every stored content
    column (semantic_origin and all others) byte-identical, at the API and in the database; each version keeps its
    own content; the earlier releases still verify their signed content digest (REQUIREMENT_CONTENT_DIGEST).
Concurrent publication is covered by tests/test_publication_content_locking.py.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select, text

from conftest import H

TAG = secrets.token_hex(3).upper()
INSTR = f"RS-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
# Deliberately not in locator order: the API must not depend on insertion order either.
PROVISIONS = [
    {"locator": "Art. 9(3)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "test the plan",
     "regulated_object": "ICT continuity plan", "timing": "annually", "normalized_meaning": "Test the plan annually.",
     "evidence_expectation": "Test report"},
    {"locator": "Art. 9(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain a plan",
     "regulated_object": "ICT continuity plan", "timing": "continuous", "normalized_meaning": "Maintain a plan.",
     "evidence_expectation": "Plan"},
    {"locator": "Art. 9(2)", "duty_bearer": "MANAGEMENT_BODY", "modality": "MUST", "action": "approve the plan",
     "regulated_object": "ICT continuity plan", "timing": None, "normalized_meaning": "Approve the plan.",
     "evidence_expectation": None},
]
VECTORS = [
    {"vector_id": "RS-POS", "vector_class": "POSITIVE", "description": "in scope", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "APPLIES"},
    {"vector_id": "RS-NEG", "vector_class": "NEGATIVE", "description": "out of scope", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "RS-UNK", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"},
]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title=f"Supersession probe instrument {INSTR}", jurisdiction="EU",
                          authority="Probe Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    return True


def _source(client, text_, locator):
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Probe Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Supersession probe (synthetic)", "locator": locator, "raw_text": text_, "instrument_identifier": INSTR})
    assert r.status_code == 200, r.text
    return r.json()


def _version(client, source, key, valid_from, provisions=PROVISIONS):
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
        "title": "ICT business continuity plan", "locator": "Art. 9", "valid_from": valid_from, "provisions": provisions})
    assert r.status_code == 201, r.text
    v = r.json()
    assert client.post(f"/api/catalog/requirements/{v['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return client.get(f"/api/catalog/requirements/{v['requirement_version_id']}", headers=H(AUTHOR)).json()


def _get(client, rv_id):
    r = client.get(f"/api/catalog/requirements/{rv_id}", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    return r.json()


def _content(rv: dict) -> dict:
    """Everything but the lifecycle codes, at both levels (as the CI-failing test compares)."""
    return {**{k: v for k, v in rv.items() if k not in ("lifecycle", "provisions", "source", "versions_total")},
            "provisions": [{k: v for k, v in p.items() if k != "lifecycle"} for p in rv["provisions"]]}


def _rows(rv_id: str) -> dict:
    """The stored rows of one version, every column but lifecycle, provisions keyed by ID (order-free)."""
    from app.db import AdminSession
    from app.models import Provision, RequirementVersion
    from app.platform import to_dict
    with AdminSession() as db:
        rv = to_dict(db.get(RequirementVersion, rv_id))
        provs = {p.provision_id: {k: v for k, v in to_dict(p).items() if k != "lifecycle"}
                 for p in db.execute(select(Provision).where(Provision.requirement_version_id == rv_id)).scalars()}
    return {"requirement": {k: v for k, v in rv.items() if k != "lifecycle"}, "provisions": provs}


def _relocate_rows(rv_id: str, newest_first: bool = True) -> None:
    """Rewrite each provision row of a version with its own values, as a lifecycle UPDATE does: on PostgreSQL every
    UPDATE writes a new row version, so this changes where (and so in which order) the rows are stored."""
    from app.db import AdminSession
    with AdminSession() as db:
        ids = db.execute(text("SELECT provision_id FROM catalog_provision WHERE requirement_version_id = :r "
                              "ORDER BY locator DESC" if newest_first else "SELECT provision_id FROM catalog_provision "
                              "WHERE requirement_version_id = :r ORDER BY locator"), {"r": rv_id}).scalars().all()
        for pid in ids:
            db.execute(text("UPDATE catalog_provision SET locator = locator WHERE provision_id = :p"), {"p": pid})
        db.commit()


def _relocate_ids(ids) -> None:
    """Rewrite these provision rows with their own values, in this order (on PostgreSQL each UPDATE writes a new row
    version, which usually, but not necessarily, lands after the others)."""
    from app.db import AdminSession
    with AdminSession() as db:
        for pid in ids:
            db.execute(text("UPDATE catalog_provision SET locator = locator WHERE provision_id = :p"), {"p": pid})
        db.commit()


def _store_in_id_order(ids) -> None:
    """Store provision rows physically in provision-ID order, exactly, so that a query without ORDER BY returns them in
    ID order whatever plan it uses (table scan, or the primary-key index). PostgreSQL: the table is rewritten in
    primary-key order (CLUSTER; no foreign key references catalog_provision). SQLite: these rows are re-inserted with
    their own values in ID order, so their rowids ascend in that order."""
    from app.db import IS_POSTGRES, AdminSession
    with AdminSession() as db:
        if IS_POSTGRES:
            db.execute(text("CLUSTER catalog_provision USING catalog_provision_pkey"))
        else:
            for pid in sorted(ids):
                row = dict(db.execute(text("SELECT * FROM catalog_provision WHERE provision_id = :p"),
                                      {"p": pid}).mappings().one())
                db.execute(text("DELETE FROM catalog_provision WHERE provision_id = :p"), {"p": pid})
                db.execute(text(f"INSERT INTO catalog_provision ({', '.join(row)}) "
                                f"VALUES ({', '.join(':' + c for c in row)})"), row)
        db.commit()


def _version_not_in_id_order(client, source, key_prefix, valid_from="2025-01-01"):
    """A verified version whose provision-ID order is not its locator order, so stored in ID order its rows are not
    in the order the API must return. IDs are random, so a few attempts may be needed (each with its own key)."""
    for attempt in range(20):
        v = _version(client, source, f"{key_prefix}{attempt}", valid_from)
        by_id = [p["locator"] for p in sorted(v["provisions"], key=lambda p: p["provision_id"])]
        if by_id != SORTED:
            return v
    raise AssertionError("no version with provision IDs out of locator order in 20 attempts")


def _definition(provision_ids, name="CONTINUITY") -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RS_{TAG}_{name}", title="Maintain the ICT continuity plan (probe)",
                                 instrument_id=inst.instrument_id, provision_ids=list(provision_ids),
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _repoint(odef_id, rv):
    from app.db import AdminSession
    from app.models import ObligationDefinition
    with AdminSession() as db:  # content authoring points the definition at the new version's provisions
        db.get(ObligationDefinition, odef_id).provision_ids = [p["provision_id"] for p in rv["provisions"]]
        db.commit()


def _release(client, odef_id, rv, source, tag, valid_from):
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    ast = {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}
    p0, p1 = rv["provisions"][0], rv["provisions"][1]
    m = lambda sid, p: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],  # noqa: E731
                        "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                        "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": ast, "golden_vectors": VECTORS,
        "source_mappings": [m(f"sm_{tag}_root", p0), m(f"sm_{tag}_1", p0), m(f"sm_{tag}_2", p1)],
        "valid_from": valid_from, "change_class": "EXECUTABLE_OUTCOME"})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"rs-{TAG}-{tag}", "changelog": tag})
    assert r.status_code == 200, r.text
    return r.json()


def _digest_check(client, content_release_id) -> str:
    r = client.get(f"/api/catalog/releases/{content_release_id}/verify", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    return {c["check"]: c["status"] for c in r.json()["checks"]}["REQUIREMENT_CONTENT_DIGEST"]


# ------------------------------------------------------------------------------------------------ tests
def test_provisions_are_listed_in_a_stable_order_whatever_the_physical_row_order(client, world):
    s = _source(client, "Entities shall maintain, approve and test a continuity plan.", f"OJ L {TAG}/order")
    v = _version_not_in_id_order(client, s, f"RS-{TAG}-ORDER")
    _store_in_id_order([p["provision_id"] for p in v["provisions"]])  # not the order the API must return
    listed = _get(client, v["requirement_version_id"])["provisions"]
    assert [p["locator"] for p in listed] == SORTED  # (locator, provision_id)
    assert listed == v["provisions"]
    for newest_first in (True, False):
        _relocate_rows(v["requirement_version_id"], newest_first)
        assert _get(client, v["requirement_version_id"])["provisions"] == listed


def test_superseding_changes_lifecycle_only_and_later_releases_never_rewrite_history(client, world):
    s = _source(client, "Entities shall maintain, approve and test an ICT continuity plan.", f"OJ L {TAG}/chain")
    key = f"RS-{TAG}-CHAIN"
    v1 = _version(client, s, key, "2025-01-01")
    odef_id = _definition(p["provision_id"] for p in v1["provisions"])
    r1 = _release(client, odef_id, v1, s, "g1", "2025-01-01")
    assert r1["requirement_versions"] == {"published": [v1["requirement_version_id"]], "superseded": []}
    v1_api, v1_rows = _get(client, v1["requirement_version_id"]), _rows(v1["requirement_version_id"])
    assert v1_api["lifecycle"] == "PUBLISHED" and {p["semantic_origin"] for p in v1_api["provisions"]} == {"ZOIKO_ENCODED"}

    # v2 has its own, different content.
    v2_provisions = [{**p, "normalized_meaning": p["normalized_meaning"] + " (v2)"} for p in PROVISIONS]
    v2 = _version(client, s, key, "2026-01-01", v2_provisions)
    _repoint(odef_id, v2)
    # The condition the CI run hit: v1's rows are rewritten in the reverse of the order just listed before the
    # supersession UPDATE rewrites them again. Where PostgreSQL then stores them is not fully controllable, so this
    # test is about content (which was never rewritten); the order itself is proved exactly by the tests below.
    _relocate_ids([p["provision_id"] for p in reversed(v1_api["provisions"])])
    r2 = _release(client, odef_id, v2, s, "g2", "2026-01-01")
    assert r2["requirement_versions"] == {"published": [v2["requirement_version_id"]],
                                          "superseded": [v1["requirement_version_id"]]}
    old = _get(client, v1["requirement_version_id"])
    assert old["lifecycle"] == "SUPERSEDED" and {p["lifecycle"] for p in old["provisions"]} == {"SUPERSEDED"}
    assert _content(old) == _content(v1_api)  # the API view, as the CI-failing test compares it
    assert _rows(v1["requirement_version_id"]) == v1_rows  # every stored column but lifecycle, incl. semantic_origin
    v2_api, v2_rows = _get(client, v2["requirement_version_id"]), _rows(v2["requirement_version_id"])
    assert v2_api["lifecycle"] == "PUBLISHED"
    assert sorted(p["normalized_meaning"] for p in v2_api["provisions"]) == \
        sorted(p["normalized_meaning"] for p in v2_provisions)  # its own content, not v1's
    assert not {p["provision_id"] for p in v2_api["provisions"]} & {p["provision_id"] for p in v1_api["provisions"]}

    # A third release: v2 is superseded in turn; v1 and v2 keep their content; nothing goes back to v1.
    v3 = _version(client, s, key, "2027-01-01")
    _repoint(odef_id, v3)
    r3 = _release(client, odef_id, v3, s, "g3", "2027-01-01")
    assert r3["requirement_versions"] == {"published": [v3["requirement_version_id"]],
                                          "superseded": [v2["requirement_version_id"]]}
    assert _rows(v1["requirement_version_id"]) == v1_rows and _rows(v2["requirement_version_id"]) == v2_rows
    assert _content(_get(client, v1["requirement_version_id"])) == _content(v1_api)
    assert _content(_get(client, v2["requirement_version_id"])) == _content(v2_api)
    assert [_get(client, v["requirement_version_id"])["lifecycle"] for v in (v1, v2, v3)] == \
        ["SUPERSEDED", "SUPERSEDED", "PUBLISHED"]
    # The signed content digests of the earlier releases still match the stored content.
    for r in (r1, r2, r3):
        assert _digest_check(client, r["content_release"]["content_release_id"]) == "PASS"


# ------------------------------------------------------------------------------------------------ processing and listing order
SORTED = ["Art. 9(1)", "Art. 9(2)", "Art. 9(3)"]


def _spy_moves(monkeypatch) -> list:
    """Every provision lifecycle move publish_bound makes, in the order it makes them (each move is one audit record)."""
    import app.engines.catalog as cat
    real, seen = cat.move, []

    def spy(db, obj, **kw):
        if kw.get("object_type") == "Provision" and kw.get("to") in ("PUBLISHED", "SUPERSEDED"):
            seen.append((kw["to"], obj.requirement_version_id, obj.locator))
        return real(db, obj, **kw)
    monkeypatch.setattr(cat, "move", spy)
    return seen


def test_publication_and_supersession_process_provisions_in_a_stable_order(client, world, monkeypatch):
    """publish_bound moves (and audits) provisions in (locator, provision_id) order however the rows are stored: before
    each release the rows are stored in provision-ID order, which for v1 is not its locator order."""
    s = _source(client, "Entities shall maintain, approve and test a continuity plan (order).", f"OJ L {TAG}/moves")
    v1 = _version_not_in_id_order(client, s, f"RS-{TAG}-MOVES")
    key = v1["requirement_key"]
    v1_ids = [p["provision_id"] for p in v1["provisions"]]
    _store_in_id_order(v1_ids)
    odef_id = _definition(v1_ids, name="MOVES")
    seen = _spy_moves(monkeypatch)
    r1 = _release(client, odef_id, v1, s, "m1", "2025-01-01")
    assert [loc for to, _, loc in seen if to == "PUBLISHED"] == SORTED
    v2 = _version(client, s, key, "2026-01-01")
    _store_in_id_order(v1_ids + [p["provision_id"] for p in v2["provisions"]])
    _repoint(odef_id, v2)
    seen.clear()
    r2 = _release(client, odef_id, v2, s, "m2", "2026-01-01")
    assert [(to, rv) for to, rv, _ in seen] == [("PUBLISHED", v2["requirement_version_id"])] * 3 + \
        [("SUPERSEDED", v1["requirement_version_id"])] * 3
    assert [loc for to, _, loc in seen if to == "PUBLISHED"] == SORTED
    assert [loc for to, _, loc in seen if to == "SUPERSEDED"] == SORTED
    # One audit record per provision and transition, at its release's record sequence: nothing doubled.
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        for p in v1["provisions"]:
            moves = [(a.prior_state, a.new_state, a.record_seq) for a in db.execute(select(AuditEvent).where(
                AuditEvent.action == "provision.lifecycle", AuditEvent.object_id == p["provision_id"])
                .order_by(AuditEvent.record_seq)).scalars()]
            assert [m[:2] for m in moves] == [("REVIEW_REQUIRED", "VERIFIED"), ("VERIFIED", "PUBLISHED"),
                                              ("PUBLISHED", "SUPERSEDED")], moves
            assert moves[1][2] == r1["rule_version"]["record_seq"] and moves[2][2] == r2["rule_version"]["record_seq"]


def test_obligation_definition_list_orders_definitions_by_key_and_provisions_by_link_order(client, world):
    s = _source(client, "Entities shall maintain, approve and test a continuity plan (list).", f"OJ L {TAG}/list")
    v = _version(client, s, f"RS-{TAG}-LIST", "2025-01-01")
    ids = sorted(p["provision_id"] for p in v["provisions"])
    # The link order is the reverse of the ID order and the rows are stored in ID order, so neither a table scan nor
    # a primary-key index scan can return the link order by accident.
    link_order = list(reversed(ids))
    _store_in_id_order(ids)
    odef_id = _definition(link_order, name="LIST")

    def listed():
        r = client.get("/api/catalog/obligation-definitions", headers=H(AUTHOR))
        assert r.status_code == 200, r.text
        return r.json()
    first = listed()
    # Ordered by key as the database collates it (PostgreSQL's collation is not Python's byte order).
    from app.db import AdminSession
    from app.models import ObligationDefinition
    with AdminSession() as db:
        by_key = [tuple(r) for r in db.execute(select(ObligationDefinition.key, ObligationDefinition.obligation_definition_id)
                                               .order_by(ObligationDefinition.key,
                                                         ObligationDefinition.obligation_definition_id)).all()]
    assert [(d["key"], d["obligation_definition_id"]) for d in first] == by_key
    mine = next(d for d in first if d["obligation_definition_id"] == odef_id)
    assert [p["provision_id"] for p in mine["provisions"]] == link_order
    detail = client.get(f"/api/catalog/obligation-definitions/{odef_id}", headers=H(AUTHOR)).json()
    assert [link["provision_id"] for link in detail["links"]] == link_order  # the list agrees with the detail view
    for order in (link_order, ids):
        _relocate_ids(order)
        assert listed() == first


def test_the_obligation_dossier_lists_its_definition_provisions_in_link_order(client):
    """On a seeded obligation whose definition links several provisions: the dossier follows the definition's link
    order. For this test the link order is set to the reverse of the provision-ID order and the rows are stored in ID
    order, so neither a table scan nor a primary-key index scan can produce it by accident; the seeded order is
    restored afterwards."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance
    tenant = "tenant-zoiko-group"
    with AdminSession() as db:
        pick = None
        for o in db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant,
                                                             ObligationInstance.record_to_seq.is_(None))
                            .order_by(ObligationInstance.lineage_id)).scalars():
            od = db.get(ObligationDefinition, o.obligation_definition_id)
            if len(od.provision_ids or []) >= 2:
                pick = (o.lineage_id, od.obligation_definition_id, list(od.provision_ids))
                break
    assert pick is not None, "the seed has an obligation whose definition links two or more provisions"
    lineage, odef_id, seeded = pick

    def dossier():
        r = client.get(f"/api/obligations/{lineage}", headers=H("actor-cco"))
        assert r.status_code == 200, r.text
        return [p["provision_id"] for p in r.json()["provisions"]]

    def set_links(order):
        with AdminSession() as db:
            db.get(ObligationDefinition, odef_id).provision_ids = list(order)
            db.commit()
    assert dossier() == seeded
    ids = sorted(seeded)
    try:
        set_links(list(reversed(ids)))
        _store_in_id_order(ids)
        assert dossier() == list(reversed(ids))
    finally:
        set_links(seeded)
    assert dossier() == seeded
