"""Regulatory Change Inbox read model, GET /api/catalog/inbox (ZA-PRD-001 §13 WF-01; engines/catalog.change_inbox_items).

What is pinned here:
  * one item per source snapshot, with its regime (from the requirement versions grounded in it), authority, dates,
    ingestion time, requirement versions and their release status, linked obligation definitions, the signed releases
    binding them and pending rule drafts (ZA-DOM-001 §4/§13, ZA-RUL-001 §02, ZA-GLS-001 §04);
  * shadow impact exactly as recorded (APP-REQ-014 aggregate buckets), labelled COMPLETED / INCOMPLETE / NOT_RECORDED,
    with "not recorded" as null and never as 0;
  * content operations only: organisation principals, external auditors and the Super Admin are refused, and no tenant
    identity appears in the response (API-001 §17; INV-14);
  * allowlisted typed filters, a stable total order (record_seq desc, source_id) with a pinned watermark across pages
    (API-REQ-012/023), the existing cursor and error conventions of GET /catalog/requirements;
  * reading the inbox evaluates, shadows and writes nothing, and its query count does not grow with the page size.

Every test builds its own instruments, sources and drafts under a per-run authority; the seeded catalogue is read only.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import event, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR, INSTR_B = f"INB-{TAG}-REG", f"INB-{TAG}-OTHER"
AUTHORITY = f"Inbox Probe Authority {TAG}"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TENANT, ORG, SUPER = f"tenant-inb-{TAG.lower()}", f"inb-cco-{TAG.lower()}", f"inb-super-{TAG.lower()}"
SUBJECT = f"subj-inb-{TAG.lower()}"
URL = "/api/catalog/inbox"

PROVISIONS = [
    {"locator": "Art. 4(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain a register",
     "regulated_object": "ICT third-party arrangements", "normalized_meaning": "Maintain a register."},
    {"locator": "Art. 4(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "review the register",
     "regulated_object": "ICT third-party register", "normalized_meaning": "Review the register annually."},
]
VECTORS = [
    {"vector_id": "IN-POS", "vector_class": "POSITIVE", "description": "EU credit institution", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "APPLIES"},
    {"vector_id": "IN-NEG", "vector_class": "NEGATIVE", "description": "Not in the EU", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"},
     "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "IN-UNK", "vector_class": "UNKNOWN", "description": "Type unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"},
]


@pytest.fixture(scope="module")
def world(client):
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for ident in (INSTR, INSTR_B):
            db.add(Instrument(identifier=ident, title=f"Inbox probe instrument {ident}", jurisdiction="EU",
                              authority=AUTHORITY, coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        if db.get(Tenant, TENANT) is None:
            db.add(Tenant(tenant_id=TENANT, legal_name=f"{TENANT} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=f"entity-{TENANT}", tenant_id=TENANT, legal_name=f"{TENANT} Bank AG",
                               country="DE", home_cell_id=CELL_ID, subject_id=SUBJECT))
            db.add(Subject(subject_id=SUBJECT, tenant_id=TENANT, entity_id=f"entity-{TENANT}",
                           subject_type="LEGAL_ENTITY", display_name=f"{TENANT} Bank AG"))
        for aid, tid, roles in ((ORG, TENANT, ["CCO"]), (SUPER, None, ["SUPER_ADMIN"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _source(client, text, *, locator=None, instrument=INSTR, jurisdiction="EU", **extra):
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": AUTHORITY, "jurisdiction": jurisdiction, "source_type": "PRIMARY_LEGISLATION",
        "title": "Inbox probe regulation (synthetic)", "locator": locator or f"OJ L {secrets.token_hex(4)}",
        "raw_text": text, "instrument_identifier": instrument, "publication_date": "2024-12-01",
        "effective_date": "2025-01-17", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _requirement(client, source_id, key, *, instrument=INSTR):
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source_id, "instrument_identifier": instrument, "requirement_key": key,
        "title": "Register of ICT third-party arrangements", "locator": "Art. 4", "valid_from": "2025-01-01",
        "provisions": PROVISIONS})
    assert r.status_code == 201, r.text
    return r.json()


def _ast(tag):
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}


def _draft(client, odef_id, tag, rv, source):
    p0, p1 = rv["provisions"]
    m = lambda sid, p: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],  # noqa: E731
                        "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                        "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": VECTORS,
        "source_mappings": [m(f"sm_{tag}_root", p0), m(f"sm_{tag}_1", p0), m(f"sm_{tag}_2", p1)],
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME"})
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


@pytest.fixture(scope="module")
def chain(client, world):
    """S1 -> v1 (released in R1 with a real shadow run) -> definition; S2 = a changed second snapshot of S1 with v2 under
    review; S0 = an unclassified snapshot with no requirement content; D2 = a pending draft with no shadow run."""
    loc = f"OJ L {TAG}/inbox"
    s1 = _source(client, "Financial entities shall maintain a register of ICT arrangements.", locator=loc)
    v1 = _requirement(client, s1["source_id"], f"INB-{TAG}-REGISTER")
    links = [{"requirement_version_id": v1["requirement_version_id"], "provision_id": p["provision_id"]}
             for p in v1["provisions"]]
    od = client.post("/api/catalog/obligation-definitions", headers=H(AUTHOR), json={
        "key": f"INB_{TAG}_REGISTER", "title": "Maintain the ICT register (probe)", "instrument_identifier": INSTR,
        "subject_type": "LEGAL_ENTITY", "links": links})
    assert od.status_code == 201, od.text
    odef_id = od.json()["obligation_definition_id"]
    d1 = _draft(client, odef_id, "inb1", v1, s1)
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{d1}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    assert client.post(f"/api/catalog/requirements/{v1['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    pub = client.post(f"/api/rules/drafts/{d1}/publish", headers=H(RELEASE),
                      json={"label": f"inb-{TAG}-r1", "changelog": "inbox probe"})
    assert pub.status_code == 200, pub.text
    s2 = _source(client, "Financial entities shall maintain and annually review a register of ICT arrangements.",
                 locator=loc)
    v2 = _requirement(client, s2["source_id"], f"INB-{TAG}-REGISTER")
    s0 = _source(client, "Entities shall notify incidents.", instrument="")
    d2 = _draft(client, odef_id, "inb2", v1, s1)
    return {"s0": s0, "s1": s1, "s2": s2, "v1": v1, "v2": v2, "odef_id": odef_id, "d1": d1, "d2": d2,
            "release": pub.json()["content_release"]}


def _items(client, actor=AUTHOR, **params):
    r = client.get(URL, headers=H(actor), params={"authority": AUTHORITY, "limit": 200, **params})
    assert r.status_code == 200, r.text
    return {i["source"]["source_id"]: i for i in r.json()["items"]}


# ------------------------------------------------------------------------------------------- relationships
def test_items_relate_each_snapshot_to_its_regime_requirements_definitions_and_releases(client, chain):
    s0, s1, s2, v1, v2 = (chain[k] for k in ("s0", "s1", "s2", "v1", "v2"))
    rel = chain["release"]["content_release_id"]
    items = _items(client)
    assert {s0["source_id"], s1["source_id"], s2["source_id"]} <= set(items)
    one = items[s1["source_id"]]
    detail = client.get(f"/api/catalog/sources/{s1['source_id']}", headers=H(AUTHOR)).json()
    for k in ("authority", "jurisdiction", "title", "locator", "fingerprint", "publication_date", "effective_date",
              "lifecycle", "record_seq", "rights_class"):
        assert one["source"][k] == detail[k], k
    assert one["source"]["fetched_at"] and "raw_text" not in one["source"]
    assert [r["identifier"] for r in one["regimes"]] == [INSTR]
    [rv] = one["requirement_versions"]
    assert (rv["requirement_version_id"], rv["lifecycle"], rv["version_number"], rv["provision_count"]) == (
        v1["requirement_version_id"], "PUBLISHED", 1, 2)
    assert rv["released_in"] == [rel] and rv["content_release_id"] == rel and rv["reviewed_by"] == LEGAL
    [od] = one["obligation_definitions"]
    assert od["obligation_definition_id"] == chain["odef_id"]
    assert od["linked_provision_ids"] == [p["provision_id"] for p in v1["provisions"]]
    [cr] = one["content_releases"]
    assert (cr["content_release_id"], cr["change_class"], cr["is_correction"], cr["status"]) == (
        rel, "EXECUTABLE_OUTCOME", False, "PUBLISHED")
    assert cr["binds_requirement_version_ids"] == [v1["requirement_version_id"]]
    assert cr["first_published_requirement_version_ids"] == [v1["requirement_version_id"]]
    assert [d["draft_id"] for d in one["pending_rule_drafts"]] == [chain["d2"]]  # the published draft is not pending
    assert one["snapshot"] == {"number": 1, "of": 2, "previous_source_id": None, "fingerprint_changed": None}
    # The changed second snapshot: fingerprint comparison with its predecessor, the new version under review.
    two = items[s2["source_id"]]
    assert two["snapshot"] == {"number": 2, "of": 2, "previous_source_id": s1["source_id"], "fingerprint_changed": True}
    [rv2] = two["requirement_versions"]
    assert (rv2["requirement_version_id"], rv2["lifecycle"], rv2["version_number"]) == (
        v2["requirement_version_id"], "REVIEW_REQUIRED", 2)
    assert rv2["released_in"] == [] and rv2["content_release_id"] is None and two["content_releases"] == []
    assert two["obligation_definitions"] == [] and two["pending_rule_drafts"] == []
    # No requirement content yet: the regime is unknown (null), not an empty regime.
    zero = items[s0["source_id"]]
    assert zero["source"]["lifecycle"] == "INGESTED" and zero["regimes"] is None
    assert zero["requirement_versions"] == zero["content_releases"] == zero["pending_rule_drafts"] == []


def test_release_shadow_impact_is_reported_exactly_as_recorded(client, chain):
    from app.db import AdminSession
    from app.models import ContentRelease
    with AdminSession() as db:
        recorded = db.get(ContentRelease, chain["release"]["content_release_id"]).shadow_summary
    [cr] = _items(client)[chain["s1"]["source_id"]]["content_releases"]
    impact = cr["shadow_impact"]
    assert impact["status"] == "COMPLETED" and impact["reasons"] == []
    for k in ("subjects_evaluated", "adopted_determination_conflicts", "execution_errors", "completed_at"):
        assert impact[k] == recorded[k], k
    assert impact["transition_buckets"] == recorded["transition_buckets"]
    assert impact["missing_fact_counts"] == recorded["missing_fact_counts"]
    assert impact["subjects_evaluated"] >= 1 and impact["execution_errors"] == 0  # the probe subject at least


def test_pending_draft_shadow_runs_are_labelled_recorded_incomplete_or_not_recorded(client, chain):
    from app.db import AdminSession
    from app.models import RuleDraft

    def draft_impact():
        [d] = _items(client)[chain["s1"]["source_id"]]["pending_rule_drafts"]
        assert d["draft_id"] == chain["d2"] and d["status"] == "DRAFT"
        return d["shadow_impact"]

    def store(report):
        with AdminSession() as db:
            db.get(RuleDraft, chain["d2"]).shadow_report = report
            db.commit()
    nothing = draft_impact()
    assert nothing["status"] == "NOT_RECORDED" and nothing["reasons"] == []
    assert all(nothing[k] is None for k in ("subjects_evaluated", "transition_buckets", "missing_fact_counts",
                                            "adopted_determination_conflicts", "execution_errors", "completed_at"))
    full = {"subjects_evaluated": 4, "transition_buckets": {"NONE->APPLIES": 1, "NONE->UNCERTAIN": 1},
            "missing_fact_counts": {"entity.financial_entity_type": 1}, "adopted_determination_conflicts": 0,
            "execution_errors": 2, "completed_at": "2026-11-16T10:00:00+00:00"}
    try:
        store(full)
        failed = draft_impact()  # two subjects could not be evaluated: the figures cover the other two only
        assert failed["status"] == "INCOMPLETE" and failed["reasons"] == ["EXECUTION_ERRORS"]
        assert (failed["execution_errors"], failed["subjects_evaluated"]) == (2, 4)
        assert failed["missing_fact_counts"] == {"entity.financial_entity_type": 1}
        store({"subjects_evaluated": 0, "note": "No prior customer state"})  # the shape of the seeded summaries
        partial = draft_impact()
        assert partial["status"] == "INCOMPLETE" and partial["subjects_evaluated"] == 0
        assert partial["transition_buckets"] is None and partial["missing_fact_counts"] is None  # not recorded != 0
        assert set(partial["reasons"]) == {"ADOPTED_DETERMINATION_CONFLICTS_NOT_RECORDED", "EXECUTION_ERRORS_NOT_RECORDED",
                                           "TRANSITION_BUCKETS_NOT_RECORDED", "MISSING_FACT_COUNTS_NOT_RECORDED",
                                           "COMPLETED_AT_NOT_RECORDED"}
        store({**full, "execution_errors": 0, "transition_buckets": {}, "missing_fact_counts": {}})
        zero = draft_impact()  # a completed run with genuine zeros keeps them as 0 / {}
        assert zero["status"] == "COMPLETED" and zero["missing_fact_counts"] == {} and zero["execution_errors"] == 0
        store({**full, "execution_errors": True, "subjects_evaluated": -1, "missing_fact_counts": {"x": "3"}})
        bad = draft_impact()  # malformed values are not recorded counts
        assert bad["status"] == "INCOMPLETE" and bad["execution_errors"] is None and bad["subjects_evaluated"] is None
        assert bad["missing_fact_counts"] is None
    finally:
        store(None)
    assert draft_impact()["status"] == "NOT_RECORDED"


# ------------------------------------------------------------------------------------------- access
def test_only_content_operations_can_read_the_inbox(client, chain):
    for actor in (AUTHOR, PEER, LEGAL, RELEASE):
        assert client.get(URL, headers=H(actor), params={"authority": AUTHORITY}).status_code == 200, actor
    before = db_fingerprint()
    for actor in (ORG, "actor-cco", "actor-ext-auditor", "actor-admin", SUPER):
        r = client.get(URL, headers=H(actor), params={"authority": AUTHORITY})
        assert r.status_code == 403, (actor, r.status_code, r.text)
        assert "items" not in r.text
    assert db_fingerprint() == before  # a refusal writes nothing but its own denial record
    assert client.get(URL).status_code == 401


def test_no_tenant_identity_reaches_content_operations(client, chain):
    from app.db import AdminSession
    from app.models import Subject, Tenant
    with AdminSession() as db:
        tenants = list(db.execute(select(Tenant.tenant_id)).scalars())
        subjects = list(db.execute(select(Subject.subject_id)).scalars())
    text = client.get(URL, headers=H(AUTHOR), params={"limit": 200}).text
    assert TENANT in tenants and SUBJECT in subjects
    leaked = [x for x in tenants + subjects if x in text]
    assert leaked == []  # the shadow figures counted these subjects, as numbers only
    # A cursor is bound to the inbox: it is not accepted by another collection, nor another collection's by the inbox.
    page = client.get(URL, headers=H(AUTHOR), params={"authority": AUTHORITY, "limit": 1}).json()
    other = client.get("/api/catalog/requirements", headers=H(AUTHOR), params={"limit": 1}).json()
    assert page["page"]["next_cursor"] and other["page"]["next_cursor"]
    r = client.get("/api/catalog/requirements", headers=H(AUTHOR), params={"limit": 1, "cursor": page["page"]["next_cursor"]})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"
    r = client.get(URL, headers=H(AUTHOR), params={"authority": AUTHORITY, "limit": 1,
                                                    "cursor": other["page"]["next_cursor"]})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"


# ------------------------------------------------------------------------------------------- filters
def test_filters_are_allowlisted_typed_and_exact(client, chain):
    s0, s1, s2 = (chain[k]["source_id"] for k in ("s0", "s1", "s2"))
    assert set(_items(client, lifecycle="INGESTED")) & {s0, s1, s2} == {s0}
    assert {s1, s2} <= set(_items(client, instrument=INSTR)) and s0 not in _items(client, instrument=INSTR)
    assert not {s0, s1, s2} & set(_items(client, instrument=INSTR_B))
    assert set(_items(client, requirement_lifecycle="REVIEW_REQUIRED")) & {s0, s1, s2} == {s2}
    assert set(_items(client, requirement_lifecycle="PUBLISHED")) & {s0, s1, s2} == {s1}
    assert set(_items(client, jurisdiction="EU", instrument=INSTR, requirement_lifecycle="PUBLISHED")) & {s0, s1, s2} == {s1}
    assert _items(client, jurisdiction="UK") == {}
    for params in ({"lifecycle": "APPROVED"}, {"requirement_lifecycle": "draft"}, {"limit": 0}, {"limit": 201},
                   {"authority": "x"}, {"authority": "x" * 201}, {"jurisdiction": "x" * 33}, {"instrument": "x" * 121},
                   {"cursor": "x" * 2049}):
        r = client.get(URL, headers=H(AUTHOR), params=params)
        assert r.status_code == 422, (params, r.status_code)
    r = client.get(URL, headers=H(AUTHOR), params={"cursor": "not.a-cursor"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"


def test_an_empty_inbox_and_an_unknown_instrument_are_an_empty_page(client, world):
    for params in ({"authority": f"No Such Authority {TAG}"}, {"instrument": f"NO-SUCH-{TAG}"}):
        r = client.get(URL, headers=H(AUTHOR), params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == [] and body["page"]["next_cursor"] is None and body["page"]["has_more"] is False
        assert isinstance(body["page"]["watermark"]["record_seq"], int)


# ------------------------------------------------------------------------------------------- order and pages
def test_newest_first_with_a_unique_tie_breaker_and_a_pinned_watermark_across_pages(client, chain):
    from app.db import AdminSession
    from app.models import RegulatorySource
    auth = f"{AUTHORITY} pages"
    made = [client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": auth, "jurisdiction": "EU", "source_type": "GUIDANCE", "title": f"Page probe {i}",
        "locator": f"page-{TAG}-{i}", "raw_text": f"Entities shall comply {i}.", "instrument_identifier": INSTR}
    ).json()["source_id"] for i in range(5)]
    # Two snapshots recorded at the same sequence number: the source ID breaks the tie.
    with AdminSession() as db:
        top = db.get(RegulatorySource, made[4]).record_seq
        db.get(RegulatorySource, made[3]).record_seq = top
        db.commit()
    tied = sorted(made[3:])
    expected = tied + [made[2], made[1], made[0]]
    seen, cursor, marks = [], None, set()
    first = client.get(URL, headers=H(AUTHOR), params={"authority": auth, "limit": 2}).json()
    late = client.post("/api/catalog/sources", headers=H(AUTHOR), json={  # recorded after the page session began
        "authority": auth, "jurisdiction": "EU", "source_type": "GUIDANCE", "title": "Late", "locator": f"page-{TAG}-late",
        "raw_text": "Entities shall comply late."}).json()["source_id"]
    # Requirement content recorded after the first page for a snapshot on the last page is not part of this session.
    late_rv = _requirement(client, made[0], f"INB-{TAG}-LATE")["requirement_version_id"]
    body, last = first, None
    while True:
        seen += [i["source"]["source_id"] for i in body["items"]]
        last = next((i for i in body["items"] if i["source"]["source_id"] == made[0]), last)
        marks.add(body["page"]["watermark"]["record_seq"])
        cursor = body["page"]["next_cursor"]
        if not body["page"]["has_more"]:
            break
        body = client.get(URL, headers=H(AUTHOR), params={"authority": auth, "limit": 2, "cursor": cursor}).json()
    assert seen == expected and len(marks) == 1  # one total order, no repeats or gaps, one pinned watermark
    assert last["requirement_versions"] == [] and last["regimes"] is None
    fresh_items = client.get(URL, headers=H(AUTHOR), params={"authority": auth, "limit": 50}).json()["items"]
    assert [i["source"]["source_id"] for i in fresh_items] == [late] + expected
    assert [r["requirement_version_id"] for r in fresh_items[-1]["requirement_versions"]] == [late_rv]
    # A cursor belongs to its filters and limit.
    cur = first["page"]["next_cursor"]
    for params in ({"authority": auth, "limit": 3, "cursor": cur}, {"authority": AUTHORITY, "limit": 2, "cursor": cur},
                   {"authority": auth, "limit": 2, "cursor": cur[:-2] + ("BB" if cur.endswith("AA") else "AA")}):
        r = client.get(URL, headers=H(AUTHOR), params=params)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor", params


# ------------------------------------------------------------------------------------------- side effects and cost
def test_reading_the_inbox_evaluates_shadows_and_writes_nothing(client, chain):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        shadow_before = {d.draft_id: d.shadow_report for d in db.execute(select(RuleDraft)).scalars()}
    before = db_fingerprint()
    for actor in (AUTHOR, RELEASE):
        body = client.get(URL, headers=H(actor), params={"limit": 2}).json()
        client.get(URL, headers=H(actor), params={"limit": 2, "cursor": body["page"]["next_cursor"]})
        client.get(URL, headers=H(actor), params={"authority": AUTHORITY, "requirement_lifecycle": "PUBLISHED"})
    assert db_fingerprint() == before  # no evaluation, shadow run, audit record, outbox event or catalogue change
    with AdminSession() as db:
        assert {d.draft_id: d.shadow_report for d in db.execute(select(RuleDraft)).scalars()} == shadow_before


def test_the_query_count_does_not_grow_with_the_page_size(client, chain):
    from app import db as dbmod
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    def queries(limit):
        statements.clear()
        event.listen(dbmod.engine, "before_cursor_execute", count)
        try:
            r = client.get(URL, headers=H(AUTHOR), params={"authority": AUTHORITY, "limit": limit})
        finally:
            event.remove(dbmod.engine, "before_cursor_execute", count)
        assert r.status_code == 200, r.text
        return len(statements), len(r.json()["items"])
    small, n_small = queries(1)
    large, n_large = queries(50)
    assert n_small == 1 and n_large >= 3
    assert large == small, (small, large)  # a fixed number of queries per page: no per-item query
