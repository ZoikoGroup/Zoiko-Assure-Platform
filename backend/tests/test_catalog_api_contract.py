"""Regulatory catalogue API contract: request schemas, not-found responses, ordering, pagination context, upstream
record states, rights-restricted text and platform boundaries.

ZA-API-001 is "Proposed for G1 API-contract lock", so these tests pin behaviour the catalogue already promises in this
repository; they do not claim an approved contract. Two changes are covered:
  * every catalogue 404 names the missing resource ("requirement version not found", "content release not found");
    two routes answered a bare "Not Found", indistinguishable from an unknown route;
  * unpaginated catalogue lists (/instruments with its nested lists, /sets, /controls with its mappings, /coverage,
    and the tie-breaks of /sources and /releases) come back in a stable total order instead of physical row order,
    which a lifecycle UPDATE changes on PostgreSQL.
Everything else pins existing guarantees: unknown and server-owned fields refused on every catalogue mutation
(API-REQ-014), retries never create a second record (API-REQ-005 intent), the pinned record watermark of /requirements
across pages (API-REQ-012), half-open legal-date filtering (ZA-TIM-001), refusal of withdrawn, superseded or altered
source snapshots (ZA-GLS-001 §04, ZA-DAT-001 §05), REFERENCE_ONLY text withheld on every read path (FR-REG-005), reads
write nothing, and catalogue writes stay Zoiko-platform functions.

Every test builds its own instruments, sources and requirement versions; the seeded catalogue is read, never changed.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR, INSTR_Z, INSTR_A = f"CAC-{TAG}-REG", f"CAC-{TAG}-Z", f"CAC-{TAG}-A"
AUTHOR, LEGAL = "actor-content-analyst", "actor-content-legal"
TENANT, ORG = f"tenant-cac-{TAG.lower()}", f"cac-cco-{TAG.lower()}"

PROVISIONS = [
    {"locator": "Art. 9(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "test the plan",
     "regulated_object": "business continuity plan", "normalized_meaning": "Test the business continuity plan."},
    {"locator": "Art. 9(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "record the test",
     "regulated_object": "test results", "normalized_meaning": "Record the results of each test."},
]


@pytest.fixture(scope="module")
def world(client):
    """Three probe instruments (inserted Z before A, so physical order is not identifier order) and one organisation."""
    from app.db import AdminSession
    from app.models import Actor, Instrument, Tenant
    with AdminSession() as db:
        for ident in (INSTR, INSTR_Z, INSTR_A):
            db.add(Instrument(identifier=ident, title=f"Catalogue contract probe {ident}", jurisdiction="EU",
                              authority="Probe Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        if db.get(Tenant, TENANT) is None:
            db.add(Tenant(tenant_id=TENANT, legal_name=f"{TENANT} Group", contracting_region="EU"))
            db.flush()
        if db.get(Actor, ORG) is None:
            add_principal(db, actor_id=ORG, tenant_id=TENANT, actor_type="HUMAN", display_name=ORG, roles=["CCO"])
        db.commit()
    return True


def _source(client, text=None, *, instrument=INSTR, locator=None, **extra):
    body = {"authority": "Probe Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
            "title": "Catalogue contract probe (synthetic)", "locator": locator or f"OJ L {secrets.token_hex(4)}",
            "raw_text": text or f"Financial entities shall test their plans. Ref {secrets.token_hex(4)}.",
            "instrument_identifier": instrument, **extra}
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _requirement_body(source_id, key, *, instrument=INSTR, valid_from="2025-01-01", **extra):
    return {"source_id": source_id, "instrument_identifier": instrument, "requirement_key": key,
            "title": "Business continuity testing", "locator": "Art. 9", "valid_from": valid_from,
            "provisions": PROVISIONS, **extra}


def _requirement(client, source_id, key, **kw):
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=_requirement_body(source_id, key, **kw))
    assert r.status_code == 201, r.text
    return r.json()


def _count(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalar()


@pytest.fixture(scope="module")
def chain(client, world):
    """A classified source, a REVIEW_REQUIRED requirement version (two provisions) and an unreleased obligation
    definition linking both provisions: a valid baseline for every catalogue mutation."""
    s = _source(client)
    rv = _requirement(client, s["source_id"], f"CAC-{TAG}-BASE")
    links = [{"requirement_version_id": rv["requirement_version_id"], "provision_id": p["provision_id"]}
             for p in rv["provisions"]]
    od = client.post("/api/catalog/obligation-definitions", headers=H(AUTHOR), json={
        "key": f"CAC_{TAG}_BCP", "title": "Test the business continuity plan (probe)", "instrument_identifier": INSTR,
        "subject_type": "LEGAL_ENTITY", "links": links})
    assert od.status_code == 201, od.text
    return {"source": s, "rv": rv, "odef_id": od.json()["obligation_definition_id"], "links": links}


def _mutations(chain) -> list[tuple[str, str, dict, tuple[str, ...]]]:
    """(method, path, a body the route would accept, server-owned fields that must be refused)."""
    s, rv = chain["source"], chain["rv"]
    sid, pid = s["source_id"], rv["provisions"][0]["provision_id"]
    prov = {k: v for k, v in PROVISIONS[0].items() if k != "locator"}
    return [
        ("POST", "/api/catalog/sources",
         {"authority": "Probe Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Probe",
          "locator": f"OJ L {secrets.token_hex(4)}", "raw_text": "Entities shall act."},
         ("source_id", "fingerprint", "lifecycle", "record_seq", "rights_record", "fetched_at", "tenant_id")),
        ("POST", f"/api/catalog/sources/{sid}/classify", {"instrument_identifier": INSTR}, ("lifecycle", "record_seq")),
        ("POST", f"/api/catalog/sources/{sid}/extract",
         {"instrument_identifier": INSTR, "requirement_key": f"CAC-{TAG}-EXT", "requirement_title": "Extracted",
          "locator": "Art. 10", "valid_from": "2025-01-01"}, ("lifecycle", "origin", "record_seq")),
        ("POST", "/api/catalog/requirements", _requirement_body(sid, f"CAC-{TAG}-NEW"),
         ("requirement_version_id", "lifecycle", "origin", "record_seq", "reviewed_by", "content_release_id")),
        ("PUT", f"/api/catalog/provisions/{pid}", prov,
         ("provision_id", "lifecycle", "semantic_origin", "requirement_version_id", "locator")),
        ("POST", "/api/catalog/obligation-definitions",
         {"key": f"CAC_{TAG}_NEW", "title": "Probe definition", "instrument_identifier": INSTR,
          "subject_type": "LEGAL_ENTITY", "links": chain["links"][:1]},
         ("obligation_definition_id", "provision_ids", "instrument_id", "status")),
        ("POST", f"/api/catalog/obligation-definitions/{chain['odef_id']}/links", chain["links"][0],
         ("lifecycle", "obligation_definition_id")),
        ("POST", f"/api/catalog/obligation-definitions/{chain['odef_id']}/links/{pid}/remove", {"reason": "probe"},
         ("lifecycle", "provision_ids")),
    ]


# ------------------------------------------------------------------------------------------- request schemas
def test_every_catalogue_mutation_refuses_unknown_and_server_owned_fields_without_writing(client, chain):
    before = db_fingerprint()
    for method, path, body, owned in _mutations(chain):
        for field in ("bogus_field", *owned):
            r = client.request(method, path, headers=H(AUTHOR), json={**body, field: "PUBLISHED"})
            assert r.status_code == 422, (method, path, field, r.text)
            errs = r.json()["detail"]
            assert any(e["type"] == "extra_forbidden" and e["loc"][-1] == field for e in errs), (path, field, errs)
    # Nested request items are strict too: a provision inside a new requirement version, a link inside a definition.
    _, _, req, _ = _mutations(chain)[3]
    bad = {**req, "provisions": [{**PROVISIONS[0], "lifecycle": "VERIFIED"}]}
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=bad)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "provisions", 0, "lifecycle"]
    _, _, od, _ = _mutations(chain)[5]
    r = client.post("/api/catalog/obligation-definitions", headers=H(AUTHOR),
                    json={**od, "links": [{**chain["links"][0], "provision_lifecycle": "PUBLISHED"}]})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "provision_lifecycle"
    assert db_fingerprint() == before


def test_catalogue_writes_are_zoiko_platform_functions_refused_before_any_write(client, chain):
    before = db_fingerprint()
    for method, path, body, _ in _mutations(chain):
        r = client.request(method, path, headers=H(ORG), json=body)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    r = client.post(f"/api/catalog/requirements/{chain['rv']['requirement_version_id']}/verify", headers=H(ORG))
    assert r.status_code == 403
    assert client.get("/api/catalog/traceability/validation", headers=H(ORG)).status_code == 403
    assert db_fingerprint() == before
    # The recorded lifecycle and edit history of catalogue content is Zoiko content-operations data: an organisation
    # reader gets the versions, never the platform audit events.
    rid = chain["rv"]["requirement_version_id"]
    org = client.get(f"/api/catalog/requirements/{rid}/history", headers=H(ORG)).json()
    staff = client.get(f"/api/catalog/requirements/{rid}/history", headers=H(AUTHOR)).json()
    assert org["events"] is None and org["versions"] == staff["versions"]
    assert any(e["action"] == "requirement.create" for e in staff["events"])


# ------------------------------------------------------------------------------------------- not found
def test_every_unknown_catalogue_identifier_is_a_404_that_names_the_resource(client, chain):
    nope = f"no-such-{TAG.lower()}"
    pid = chain["rv"]["provisions"][0]["provision_id"]
    prov = {k: v for k, v in PROVISIONS[0].items() if k != "locator"}
    calls = [
        ("GET", f"/api/catalog/sources/{nope}", None, "source"),
        ("GET", f"/api/catalog/sources/{nope}/versions", None, "source"),
        ("GET", f"/api/catalog/sources/{nope}/trace", None, "source"),
        ("GET", f"/api/catalog/requirements/{nope}", None, "requirement version"),
        ("GET", f"/api/catalog/requirements/{nope}/history", None, "requirement version"),
        ("GET", f"/api/catalog/requirements/{nope}/trace", None, "requirement version"),
        ("GET", f"/api/catalog/obligation-definitions/{nope}", None, "obligation definition"),
        ("GET", f"/api/catalog/obligation-definitions/{nope}/trace", None, "obligation definition"),
        ("GET", f"/api/catalog/releases/{nope}/impact", None, "content release"),
        ("GET", f"/api/catalog/releases/{nope}/verify", None, "content release"),
        ("POST", f"/api/catalog/sources/{nope}/classify", {"instrument_identifier": INSTR}, "source"),
        ("POST", f"/api/catalog/sources/{nope}/extract",
         {"instrument_identifier": INSTR, "requirement_key": f"CAC-{TAG}-X", "requirement_title": "Xx", "locator": "A",
          "valid_from": "2025-01-01"}, "source"),
        ("POST", "/api/catalog/requirements", _requirement_body(nope, f"CAC-{TAG}-NOSRC"), "source"),
        ("POST", "/api/catalog/requirements",
         _requirement_body(chain["source"]["source_id"], f"CAC-{TAG}-NOINST", instrument=nope), "instrument"),
        ("PUT", f"/api/catalog/provisions/{nope}", prov, "provision"),
        ("POST", f"/api/catalog/requirements/{nope}/verify", None, "requirement version"),
        ("POST", f"/api/catalog/obligation-definitions/{nope}/links", chain["links"][0], "obligation definition"),
        ("POST", f"/api/catalog/obligation-definitions/{nope}/links/{pid}/remove", {"reason": "probe"},
         "obligation definition"),
    ]
    before = db_fingerprint()
    for method, path, body, resource in calls:
        headers = H(LEGAL if path.endswith("/verify") and method == "POST" else AUTHOR)
        r = client.request(method, path, headers=headers, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        detail = r.json()["detail"]
        text = detail if isinstance(detail, str) else detail.get("message", "")
        assert text != "Not Found" and f"{resource} not found" in text.lower(), (path, detail)
    assert db_fingerprint() == before
    # An unknown route is still the framework's bare 404, so the two remain distinguishable.
    assert client.get(f"/api/catalog/no-such-collection-{TAG.lower()}", headers=H(AUTHOR)).json()["detail"] == "Not Found"


# ------------------------------------------------------------------------------------------- retries
def test_a_retried_catalogue_action_never_records_a_second_change(client, world):
    from app.models import AuditEvent, Provision, RequirementVersion
    s = _source(client)
    body = {"instrument_identifier": INSTR, "requirement_key": f"CAC-{TAG}-RETRY", "requirement_title": "Retried",
            "locator": "Art. 11", "valid_from": "2025-01-01"}
    first = client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR), json=body)
    assert first.status_code == 200, first.text
    rid = first.json()["requirement_version"]["requirement_version_id"]
    counts = (_count(RequirementVersion, requirement_key=body["requirement_key"]),
              _count(Provision, requirement_version_id=rid))
    again = client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR), json=body)
    assert again.status_code == 409 and again.json()["detail"]["error"] == "DUPLICATE_REQUIREMENT_VERSION"
    assert again.json()["detail"]["requirement_version_id"] == rid
    assert (_count(RequirementVersion, requirement_key=body["requirement_key"]),
            _count(Provision, requirement_version_id=rid)) == counts
    # Verification is one transition: a repeat is a conflict and records nothing more.
    rv = _requirement(client, s["source_id"], f"CAC-{TAG}-VERIFY-ONCE")
    vid = rv["requirement_version_id"]
    assert client.post(f"/api/catalog/requirements/{vid}/verify", headers=H(LEGAL)).status_code == 200
    n = _count(AuditEvent, action="requirement.verify", object_id=vid)
    before = db_fingerprint()
    r = client.post(f"/api/catalog/requirements/{vid}/verify", headers=H(LEGAL))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "CONTENT_TRANSITION_NOT_ALLOWED"
    assert n == 1 and db_fingerprint() == before


# ------------------------------------------------------------------------------------------- upstream record states
def test_requirement_content_is_never_derived_from_a_withdrawn_superseded_or_altered_source(client, world):
    from app.db import AdminSession
    from app.models import RegulatorySource
    keep = _source(client)
    rv = _requirement(client, keep["source_id"], f"CAC-{TAG}-UPSTREAM")

    def set_source(source_id, **values):
        with AdminSession() as db:
            row = db.get(RegulatorySource, source_id)
            for k, v in values.items():
                setattr(row, k, v)
            db.commit()

    extract = {"instrument_identifier": INSTR, "requirement_key": f"CAC-{TAG}-UPX", "requirement_title": "Up",
               "locator": "Art. 12", "valid_from": "2025-01-01"}
    for state in ("WITHDRAWN", "SUPERSEDED"):
        s = _source(client)
        set_source(s["source_id"], lifecycle=state)
        before = db_fingerprint()
        for r in (client.post("/api/catalog/requirements", headers=H(AUTHOR),
                              json=_requirement_body(s["source_id"], f"CAC-{TAG}-{state}")),
                  client.post(f"/api/catalog/sources/{s['source_id']}/extract", headers=H(AUTHOR), json=extract)):
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["error"] == "SOURCE_NOT_USABLE" and r.json()["detail"]["source_id"] == s["source_id"]
        assert db_fingerprint() == before
    altered = _source(client)
    set_source(altered["source_id"], raw_text="Financial entities may test their plans.")
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR),
                    json=_requirement_body(altered["source_id"], f"CAC-{TAG}-ALTERED"))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SOURCE_FINGERPRINT_MISMATCH"
    # A source withdrawn after content was proposed from it can no longer ground a verification.
    set_source(keep["source_id"], lifecycle="WITHDRAWN")
    r = client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SOURCE_NOT_GROUNDED"
    got = client.get(f"/api/catalog/requirements/{rv['requirement_version_id']}", headers=H(AUTHOR)).json()
    assert got["lifecycle"] == "REVIEW_REQUIRED" and all(p["lifecycle"] == "REVIEW_REQUIRED" for p in got["provisions"])


# ------------------------------------------------------------------------------------------- listing semantics
def test_requirement_pages_keep_one_total_order_and_the_first_pages_watermark(client, world):
    s = _source(client, instrument=INSTR_A)
    keys = [f"CAC-{TAG}-P{i}" for i in (3, 1, 4, 2)]  # created out of order
    for k in keys:
        _requirement(client, s["source_id"], k, instrument=INSTR_A)
    params = {"instrument": INSTR_A, "limit": 1}
    first = client.get("/api/catalog/requirements", headers=H(AUTHOR), params=params).json()
    pinned = first["page"]["watermark"]["record_seq"]
    late = f"CAC-{TAG}-P9"  # sorts last, created after the page session began
    _requirement(client, s["source_id"], late, instrument=INSTR_A)
    seen, cursor = list(first["items"]), first["page"]["next_cursor"]
    while cursor:
        body = client.get("/api/catalog/requirements", headers=H(AUTHOR), params={**params, "cursor": cursor}).json()
        assert body["page"]["watermark"]["record_seq"] == pinned  # temporal context preserved across pages
        seen += body["items"]
        cursor = body["page"]["next_cursor"] if body["page"]["has_more"] else None
    order = [(i["requirement_key"], i["record_seq"], i["requirement_version_id"]) for i in seen]
    assert [k for k, _, _ in order] == sorted(keys) and order == sorted(order) and len(set(order)) == len(order)
    fresh = client.get("/api/catalog/requirements", headers=H(AUTHOR), params={"instrument": INSTR_A, "limit": 50}).json()
    assert [i["requirement_key"] for i in fresh["items"]] == sorted(keys) + [late]
    assert fresh["page"]["watermark"]["record_seq"] > pinned


def test_requirement_valid_at_is_a_half_open_legal_date_filter(client, world):
    s = _source(client, instrument=INSTR_Z)
    key = f"CAC-{TAG}-WINDOW"
    _requirement(client, s["source_id"], key, instrument=INSTR_Z, valid_from="2025-01-01", valid_to="2026-01-01")

    def listed(valid_at):
        r = client.get("/api/catalog/requirements", headers=H(AUTHOR),
                       params={"requirement_key": key, "valid_at": valid_at})
        assert r.status_code == 200, r.text
        return [i["requirement_key"] for i in r.json()["items"]]
    assert listed("2024-12-31") == [] and listed("2025-01-01") == [key] and listed("2025-12-31") == [key]
    assert listed("2026-01-01") == []  # valid_to is exclusive
    # A legal date, not an instant: a timestamp is not silently cut to a date.
    r = client.get("/api/catalog/requirements", headers=H(AUTHOR),
                   params={"requirement_key": key, "valid_at": "2025-06-01T10:00:00+05:00"})
    assert r.status_code == 422


def _ordered(stmt) -> list:
    """The database's own total order for `stmt` (text sorts by the database collation, which on PostgreSQL is
    locale-aware and differs from Python's code-point order, e.g. for '_' and '-')."""
    from app.db import AdminSession
    with AdminSession() as db:
        return list(db.execute(stmt).scalars())


def test_unpaginated_catalogue_lists_come_back_in_a_stable_total_order(client, world):
    from app.db import AdminSession
    from app.models import (ControlDefinition, FrameworkMapping, Instrument, ObligationDefinition, RegulatorySource,
                            RequirementVersion, SetVersion)
    s = _source(client, instrument=INSTR_Z)
    made = [_requirement(client, s["source_id"], f"CAC-{TAG}-ORD-{k}", instrument=INSTR_Z) for k in ("B", "A")]

    def instruments():
        r = client.get("/api/catalog/instruments", headers=H(ORG))
        assert r.status_code == 200, r.text
        return r.json()

    def check(body):
        assert [i["instrument_id"] for i in body] == _ordered(
            select(Instrument.instrument_id).order_by(Instrument.identifier, Instrument.instrument_id))
        for i in body:
            assert [r["requirement_version_id"] for r in i["requirements"]] == _ordered(
                select(RequirementVersion.requirement_version_id)
                .where(RequirementVersion.instrument_id == i["instrument_id"])
                .order_by(RequirementVersion.requirement_key, RequirementVersion.record_seq,
                          RequirementVersion.requirement_version_id)), i["identifier"]
            assert [o["obligation_definition_id"] for o in i["obligation_definitions"]] == _ordered(
                select(ObligationDefinition.obligation_definition_id)
                .where(ObligationDefinition.instrument_id == i["instrument_id"])
                .order_by(ObligationDefinition.key, ObligationDefinition.obligation_definition_id)), i["identifier"]
    body = instruments()
    check(body)
    idents = [i["identifier"] for i in body]
    assert idents.index(INSTR_A) < idents.index(INSTR_Z)  # inserted Z first: physical order is not the answer
    # A lifecycle UPDATE (verification) moves the row physically on PostgreSQL; the order must not follow it.
    assert client.post(f"/api/catalog/requirements/{made[0]['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    after = instruments()
    check(after)
    z = next(i for i in after if i["identifier"] == INSTR_Z)
    assert [r["requirement_key"] for r in z["requirements"] if "-ORD-" in r["requirement_key"]] == [
        f"CAC-{TAG}-ORD-A", f"CAC-{TAG}-ORD-B"]
    cov = [i["identifier"] for i in client.get("/api/catalog/coverage", headers=H(ORG)).json()["instruments"]]
    assert cov == [i["identifier"] for i in after]
    sets = [x["set_version_id"] for x in client.get("/api/catalog/sets", headers=H(ORG)).json()]
    assert sets and sets == _ordered(select(SetVersion.set_version_id).order_by(
        SetVersion.set_key, SetVersion.valid_from, SetVersion.set_version_id))
    controls = client.get("/api/catalog/controls", headers=H(ORG)).json()
    assert controls and [c["control_definition_id"] for c in controls] == _ordered(
        select(ControlDefinition.control_definition_id).order_by(ControlDefinition.key,
                                                                 ControlDefinition.control_definition_id))
    for c in controls:
        assert [m["id"] for m in c["framework_mappings"]] == _ordered(
            select(FrameworkMapping.id).where(FrameworkMapping.control_definition_id == c["control_definition_id"])
            .order_by(FrameworkMapping.framework, FrameworkMapping.reference, FrameworkMapping.id)), c["key"]
    # Newest first, with ties broken by record sequence (newest first), then ID.
    a, b = _source(client), _source(client)
    same = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with AdminSession() as db:
        for sid in (a["source_id"], b["source_id"]):
            db.get(RegulatorySource, sid).fetched_at = same
        db.commit()
    listed = [x["source_id"] for x in client.get("/api/catalog/sources", headers=H(AUTHOR)).json()]
    assert listed.index(b["source_id"]) < listed.index(a["source_id"])  # b has the later record sequence
    rel = client.get("/api/catalog/releases", headers=H(AUTHOR)).json()
    keys = [(r["published_at"], r["cell_received_seq"]) for r in rel]
    assert keys == sorted(keys, reverse=True)


# ------------------------------------------------------------------------------------------- rights and side effects
def test_reference_only_source_text_is_withheld_on_every_catalogue_read_path(client, world):
    secret = f"Protected clause text {secrets.token_hex(8)} shall not be displayed."
    s = _source(client, secret, rights_class="REFERENCE_ONLY")
    rv = _requirement(client, s["source_id"], f"CAC-{TAG}-REFONLY")
    rid = rv["requirement_version_id"]
    paths = ["/api/catalog/sources", f"/api/catalog/sources/{s['source_id']}",
             f"/api/catalog/sources/{s['source_id']}/versions", f"/api/catalog/sources/{s['source_id']}/trace",
             f"/api/catalog/requirements/{rid}", f"/api/catalog/requirements/{rid}/history",
             f"/api/catalog/requirements/{rid}/trace", "/api/catalog/requirements?limit=200",
             "/api/catalog/instruments"]
    for actor in (AUTHOR, ORG):
        for path in paths:
            r = client.get(path, headers=H(actor))
            assert r.status_code == 200, (actor, path, r.text)
            assert secret not in r.text, (actor, path)
    one = client.get(f"/api/catalog/sources/{s['source_id']}", headers=H(AUTHOR)).json()
    assert one["raw_text"] is None and one["fingerprint"] == s["fingerprint"] and one["fingerprint_intact"] is True


def test_catalogue_reads_write_nothing(client, chain):
    rid, sid = chain["rv"]["requirement_version_id"], chain["source"]["source_id"]
    rel = client.get("/api/catalog/releases", headers=H(AUTHOR)).json()
    paths = ["/api/catalog/instruments", "/api/catalog/sources", f"/api/catalog/sources/{sid}",
             f"/api/catalog/sources/{sid}/versions", f"/api/catalog/sources/{sid}/trace", "/api/catalog/requirements",
             f"/api/catalog/requirements/{rid}", f"/api/catalog/requirements/{rid}/history",
             f"/api/catalog/requirements/{rid}/trace", "/api/catalog/obligation-definitions",
             f"/api/catalog/obligation-definitions/{chain['odef_id']}",
             f"/api/catalog/obligation-definitions/{chain['odef_id']}/trace", "/api/catalog/fact-definitions",
             "/api/catalog/sets", "/api/catalog/controls", "/api/catalog/releases", "/api/catalog/coverage",
             "/api/catalog/traceability/validation"]
    if rel:
        paths.append(f"/api/catalog/releases/{rel[0]['content_release_id']}/impact")
    before = db_fingerprint()
    for actor in (AUTHOR, ORG):
        for path in paths:
            r = client.get(path, headers=H(actor))
            assert r.status_code in (200, 403), (actor, path, r.text)  # 403: validation is content staff only
            json.loads(r.text)
    assert db_fingerprint() == before
