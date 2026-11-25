"""Regulatory catalogue lifecycle integrity: requirement-version supersession follows valid time, and one source snapshot
is registered once even under concurrent requests.

Controlling text:
  * ZA-GLS-001 §04 regulatory content lifecycle: "SUPERSEDED  A newer published version replaces this version for future
    valid time"; PUBLISHED "Signed content release is active for declared coverage". A version whose legal valid range
    ends at or before another version begins replaces that version for no valid time at all, so publishing it (for
    example encoding an earlier period of the law after the current one was released) must not supersede it. Before,
    publish_bound superseded every other PUBLISHED version of the requirement key: the version in force from a later
    date became SUPERSEDED, lost release eligibility (RELEASABLE) and no further rule could be released on it.
    Supersession by a newer version that reaches into the older one's valid time, or follows it, is unchanged.
  * ZA-DOM-001 §4.1 "SourceSnapshot  Immutable. ... content digest": a snapshot of a source is identified by its digest,
    and POST /catalog/sources refuses to register the same snapshot of the same source twice
    (DUPLICATE_SOURCE_SNAPSHOT). The check read before it wrote with nothing serialising it unless the request named an
    instrument (that instrument's row lock), so two concurrent registrations could both pass it; they are now serialised
    on the snapshot identity (PostgreSQL transaction advisory lock; no schema change).
  * Catalogue mutation is a Zoiko content operation: an organisation principal is refused and nothing is written.
Runs on SQLite and PostgreSQL 17; the concurrency test runs on PostgreSQL 17 only (one thread is held inside the critical
section while the other is shown to wait).
"""
from __future__ import annotations

import secrets
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RLI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-rli-{TAG.lower()}", f"rli-cco-{TAG.lower()}"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
S: dict = {}


# ------------------------------------------------------------------------------------------------ world
def _ast(t: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{t}_{nid}", "operator": "EQ", "source_mappings": [f"sm_{t}_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{t}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{t}_{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        pred("eu", EU, "BOOLEAN", True), pred("kind", KIND, "ENUM", "CREDIT_INSTITUTION")]}


def _vectors(at: str) -> list[dict]:
    def vec(vid, cls, eu, kind, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": at,
                "facts": {EU: eu, KIND: kind}, "expected_outcome": out}
    return [vec("P", "POSITIVE", True, "CREDIT_INSTITUTION", "APPLIES"),
            vec("N", "NEGATIVE", False, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
            vec("U", "UNKNOWN", True, {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Lifecycle Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="RLI CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Lifecycle Act",
        "locator": f"OJ L {TAG}/rli", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    return True


def _version(client, key: str, valid_from: str, valid_to: str | None = None) -> dict:
    """A verified requirement version of ``key`` over [valid_from, valid_to) with one provision."""
    body = {"source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
            "title": "Register requirement", "locator": "Art. 1", "valid_from": valid_from, "provisions": [
                {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
                 "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
                 "evidence_expectation": "Register"}]}
    if valid_to:
        body["valid_to"] = valid_to
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=body)
    assert r.status_code == 201, r.text
    rv = r.json()
    v = client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL))
    assert v.status_code == 200, v.text
    return rv


def _release(client, rv: dict, name: str, valid_from: str, at: str):
    """Publish a rule of a new definition linked to ``rv``'s provision: the release that publishes ``rv``."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RLI_{TAG}_{name}", title=f"Lifecycle obligation {name}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = secrets.token_hex(3), rv["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(t), "golden_vectors": _vectors(at),
        "valid_from": valid_from, "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"rli-{secrets.token_hex(4)}", "changelog": "rli"})


def _states(*rvs) -> list[tuple[str, list[str]]]:
    """(lifecycle, [provision lifecycles]) of each version, as stored."""
    from app.db import AdminSession
    from app.models import Provision, RequirementVersion
    with AdminSession() as db:
        return [(db.get(RequirementVersion, rv["requirement_version_id"]).lifecycle,
                 [p.lifecycle for p in db.execute(select(Provision).where(
                     Provision.requirement_version_id == rv["requirement_version_id"])).scalars()]) for rv in rvs]


def _superseded_records(rv: dict) -> tuple[int, int]:
    """(requirement.version_superseded outbox events, SUPERSEDED lifecycle audit rows) naming this version."""
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent
    with AdminSession() as db:
        events = db.execute(select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "requirement.version_superseded",
            OutboxEvent.aggregate_id == rv["requirement_version_id"])).scalar()
        audits = db.execute(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.object_id == rv["requirement_version_id"], AuditEvent.new_state == "SUPERSEDED")).scalar()
    return events, audits


def _in_force(client, key: str, on: str) -> list[tuple[str, str]]:
    page = client.get("/api/catalog/requirements", headers=H(AUTHOR),
                      params={"requirement_key": key, "valid_at": on}).json()
    return [(i["requirement_version_id"], i["lifecycle"]) for i in page["items"]]


# ------------------------------------------------------------------------------------------------ supersession
def test_an_earlier_period_released_later_does_not_supersede_the_version_in_force_after_it(client, world):
    """The current law (from 2026) is released first; the earlier period (2024-2025) is encoded and released afterwards.
    The earlier-period version replaces the current one for no valid time, so the current one stays PUBLISHED with its
    provisions, nothing records it as superseded, each is the version in force for its own period, and the current law
    can still ground a new release. Before, it became SUPERSEDED and the third release was refused (409)."""
    key = f"RLI-{TAG}-BACKFILL"
    current = _version(client, key, "2026-01-01")
    assert _release(client, current, "CUR", "2026-01-01", "2026-06-01").status_code == 200
    earlier = _version(client, key, "2024-01-01", "2026-01-01")
    assert _release(client, earlier, "EARLIER", "2024-01-01", "2024-06-01").status_code == 200
    assert _states(current, earlier) == [("PUBLISHED", ["PUBLISHED"]), ("PUBLISHED", ["PUBLISHED"])]
    assert _superseded_records(current) == (0, 0)
    assert _in_force(client, key, "2025-06-01") == [(earlier["requirement_version_id"], "PUBLISHED")]
    assert _in_force(client, key, "2026-06-01") == [(current["requirement_version_id"], "PUBLISHED")]
    again = _release(client, current, "CUR2", "2026-01-01", "2026-06-01")
    assert again.status_code == 200, again.text


@pytest.mark.parametrize("earlier_to, superseded", [("2026-01-01", False), ("2026-01-02", True)],
                         ids=["ends-when-it-begins", "reaches-one-day-into-it"])
def test_valid_ranges_are_half_open(client, world, earlier_to, superseded):
    """[2024-01-01, 2026-01-01) ends exactly when the current version begins: no common valid day, nothing replaced.
    [2024-01-01, 2026-01-02) shares 1 January 2026 with it: a newer version that reaches into its valid time replaces it,
    as before."""
    key = f"RLI-{TAG}-EDGE-{int(superseded)}"
    current = _version(client, key, "2026-01-01")
    assert _release(client, current, f"EDGE{int(superseded)}A", "2026-01-01", "2026-06-01").status_code == 200
    earlier = _version(client, key, "2024-01-01", earlier_to)
    assert _release(client, earlier, f"EDGE{int(superseded)}B", "2024-01-01", "2024-06-01").status_code == 200
    assert _states(current)[0][0] == ("SUPERSEDED" if superseded else "PUBLISHED")
    assert _superseded_records(current) == ((1, 1) if superseded else (0, 0))


@pytest.mark.parametrize("first, second", [(("2025-01-01", None), ("2026-01-01", None)),
                                           (("2024-01-01", "2025-01-01"), ("2025-01-01", None))],
                         ids=["amendment-overlapping-the-open-version", "successor-from-the-end-date"])
def test_a_newer_version_for_the_same_or_later_valid_time_still_supersedes(client, world, first, second):
    """Unchanged behaviour: a newer published version that covers the older one's later valid time, or follows it,
    supersedes it with its provisions, records the supersession once, and leaves its content untouched."""
    from app.db import AdminSession
    from app.engines.catalog import requirement_content_digest
    from app.models import Provision, RequirementVersion
    key = f"RLI-{TAG}-AMEND-{first[0][:4]}"
    old = _version(client, key, *first)

    def digest():
        with AdminSession() as db:
            return requirement_content_digest(db.get(RequirementVersion, old["requirement_version_id"]), list(
                db.execute(select(Provision).where(Provision.requirement_version_id == old["requirement_version_id"])
                           .order_by(Provision.locator, Provision.provision_id)).scalars()))
    assert _release(client, old, f"OLD{first[0][:4]}", first[0], first[0][:4] + "-06-01").status_code == 200
    before = digest()
    new = _version(client, key, *second)
    assert _release(client, new, f"NEW{first[0][:4]}", second[0], second[0][:4] + "-06-01").status_code == 200
    assert _states(old, new) == [("SUPERSEDED", ["SUPERSEDED"]), ("PUBLISHED", ["PUBLISHED"])]
    assert _superseded_records(old) == (1, 1) and digest() == before


def test_an_organisation_principal_cannot_change_the_catalogue(client, world):
    """Catalogue content is operated by Zoiko content staff only; an organisation's CCO is refused and nothing is written
    (beyond the denial's own audit record, which the fingerprint leaves out)."""
    key = f"RLI-{TAG}-ORG"
    rv = _version(client, key, "2025-01-01")
    before = db_fingerprint()
    attempts = [
        client.post("/api/catalog/sources", headers=H(CCO), json={
            "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Org Act",
            "locator": f"OJ L {TAG}/org", "raw_text": "Organisations shall not edit the catalogue."}),
        client.post("/api/catalog/requirements", headers=H(CCO), json={
            "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
            "title": "Org", "locator": "Art. 2", "valid_from": "2025-02-01", "provisions": [
                {"locator": "Art. 2(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "act",
                 "regulated_object": "object", "normalized_meaning": "Act."}]}),
        client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(CCO)),
        client.put(f"/api/catalog/provisions/{rv['provisions'][0]['provision_id']}", headers=H(CCO), json={
            "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "act", "regulated_object": "object",
            "normalized_meaning": "Changed by an organisation."})]
    assert [a.status_code for a in attempts] == [403, 403, 403, 403], [a.text for a in attempts]
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ duplicate snapshots
def test_a_snapshot_already_registered_is_refused(client, world):
    """The same text of the same source (authority, jurisdiction, locator) is one snapshot (sequential requests)."""
    body = {"authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Twice Act",
            "locator": f"OJ L {TAG}/twice", "raw_text": "Registered once."}
    first = client.post("/api/catalog/sources", headers=H(AUTHOR), json=body)
    assert first.status_code == 200, first.text
    again = client.post("/api/catalog/sources", headers=H(AUTHOR), json={**body, "instrument_identifier": INSTR})
    assert again.status_code == 409 and again.json()["detail"]["error"] == "DUPLICATE_SOURCE_SNAPSHOT"
    assert again.json()["detail"]["source_id"] == first.json()["source_id"]


@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and locks; exact reason required by the CI gate
    return True


@pytest.mark.parametrize("first_names_instrument", [False, True], ids=["neither-names-an-instrument", "one-does"])
def test_concurrent_registrations_of_one_snapshot_register_it_once(client, world, pg, monkeypatch,
                                                                   first_names_instrument):
    """The first registration is held after its duplicate check, inside its transaction; a second registration of the
    same snapshot (no instrument named) must wait for it, then see it and be refused. Before, the second one passed the
    check too (no lock was shared: an instrument's row lock is taken only when an instrument is named) and the snapshot
    was registered twice."""
    from app.db import SessionLocal, set_principal
    from app.engines import catalog
    from app.models import Actor, RegulatorySource
    from app.routers.catalog import SourceIn, ingest_source
    from app.security import Ctx
    locator = f"OJ L {TAG}/race-{int(first_names_instrument)}"
    body = {"authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Race Act",
            "locator": locator, "raw_text": "Registered once, even when twice at once."}
    held, release, out = threading.Event(), threading.Event(), {}
    real = catalog.find_duplicate_snapshot

    def holding(*a, **k):
        r = real(*a, **k)
        if threading.current_thread().name == "first":
            held.set()
            assert release.wait(30)
        return r
    monkeypatch.setattr(catalog, "find_duplicate_snapshot", holding)

    def register(name, payload):
        try:
            with SessionLocal() as db:
                set_principal(db, None, AUTHOR)
                out[name] = ("ok", ingest_source(SourceIn(**payload), Ctx(db=db, actor=db.get(Actor, AUTHOR)))["source_id"])
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)
    first_body = {**body, "instrument_identifier": INSTR} if first_names_instrument else body
    a = threading.Thread(target=register, args=("first", first_body), name="first")
    a.start()
    assert held.wait(60), out
    b = threading.Thread(target=register, args=("second", body), name="second")
    b.start()
    b.join(2.0)
    waited = b.is_alive()
    release.set()
    a.join(60)
    b.join(60)
    assert waited and out["first"][0] == "ok", out
    assert out["second"][0] == 409 and out["second"][1]["error"] == "DUPLICATE_SOURCE_SNAPSHOT", out
    assert out["second"][1]["source_id"] == out["first"][1]
    from app.db import AdminSession
    with AdminSession() as db:
        assert db.execute(select(func.count()).select_from(RegulatorySource).where(
            RegulatorySource.locator == locator)).scalar() == 1
