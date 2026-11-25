"""Regulatory source lifecycle under concurrent requirement creation and classification.

Controlling text:
  * ZA-GLS-001 §04 regulatory content lifecycle (CLASSIFIED -> AI_EXTRACTED / REVIEW_REQUIRED; REVIEW_REQUIRED does not
    return to CLASSIFIED), applied by engines/catalog.move, which records each transition it makes and records nothing
    when there is none ("False when obj is already in to (no record)");
  * ZA-DOM-001 DOM-INV-15 (a lifecycle history that can be reconstructed): the audit trail is the source lifecycle's
    history store (engines/catalog.py module contract), so every recorded transition must be one that was committed,
    with its real prior state;
  * ZA-DAT-001 DAT-REQ-010 ("serialized mutation logic") and the lock order engines/locks.py documents (callers take the
    lock BEFORE they read the state they validate).

Defect fixed (reproduced on PostgreSQL 17 by the two concurrency tests on the unchanged code): requirement creation
(POST /catalog/requirements, POST /catalog/sources/{id}/extract) read the source without a lock and changed it with an
implicit row lock taken only when its UPDATE was flushed, after the instrument row lock and the record sequence.
  1. A second creation that read the source before the first committed kept the stale CLASSIFIED value: after waiting,
     it moved the source "CLASSIFIED -> REVIEW_REQUIRED" again and recorded a second transition that never happened
     (its prior state was already REVIEW_REQUIRED).
  2. Classification locks the source row and then the instrument; creation locked the instrument and then the source, so
     the two could deadlock (PostgreSQL aborts one with "deadlock detected").
Creation now locks the source row first (source -> instrument -> record sequence, the order classification uses) and
reads it fresh under that lock. The transition table, states, payloads and responses are unchanged.
Sequential, rollback and authorization behaviour is pinned on SQLite and PostgreSQL 17; the concurrency tests run on
PostgreSQL 17 only (one thread is held inside its critical section while the other is shown to wait).
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
INSTR, INSTR2 = f"SLC-{TAG}-REG", f"SLC-{TAG}-REG2"
AUTHOR, LEGAL = "actor-content-analyst", "actor-content-legal"
TA, CCO = f"tenant-slc-{TAG.lower()}", f"slc-cco-{TAG.lower()}"
PROVISION = {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    with AdminSession() as db:
        for ident in (INSTR, INSTR2):
            db.add(Instrument(identifier=ident, title=f"{ident} Instrument", jurisdiction="EU", authority="Authority",
                              coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="SLC CCO", roles=["CCO"])
        db.commit()
    return True


def _source(client, name: str, *, classify: bool = True) -> str:
    """A registered source snapshot, CLASSIFIED under INSTR unless ``classify`` is False (then INGESTED)."""
    body = {"authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": f"{name} Act",
            "locator": f"OJ L {TAG}/{name}", "raw_text": f"{name}: credit institutions shall keep a register."}
    if classify:
        body["instrument_identifier"] = INSTR
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["source_id"]


def _requirement_body(source_id: str, key: str, instrument: str = INSTR, valid_from: str = "2025-01-01") -> dict:
    return {"source_id": source_id, "instrument_identifier": instrument, "requirement_key": key,
            "title": "Register requirement", "locator": "Art. 1", "valid_from": valid_from, "provisions": [PROVISION]}


def _lifecycle_records(source_id: str) -> list[tuple[str, str, str]]:
    """The source's recorded lifecycle transitions (action, prior, new), in record order."""
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        return [(e.action, e.prior_state, e.new_state) for e in db.execute(select(AuditEvent).where(
            AuditEvent.object_type == "RegulatorySource", AuditEvent.object_id == source_id,
            AuditEvent.prior_state.is_not(None)).order_by(AuditEvent.record_seq, AuditEvent.id)).scalars()]


def _source_state(source_id: str) -> str:
    from app.db import AdminSession
    from app.models import RegulatorySource
    with AdminSession() as db:
        return db.get(RegulatorySource, source_id).lifecycle


def _versions(source_id: str) -> int:
    from app.db import AdminSession
    from app.models import RequirementVersion
    with AdminSession() as db:
        return db.execute(select(func.count()).select_from(RequirementVersion).where(
            RequirementVersion.source_id == source_id)).scalar()


CLASSIFIED = ("source.classify", "INGESTED", "CLASSIFIED")
PROPOSED = ("source.lifecycle", "CLASSIFIED", "REVIEW_REQUIRED")


# ------------------------------------------------------------------------------------------------ sequential
def test_creations_move_the_source_once_and_record_it_once(client, world):
    """Two requirements created one after the other from one CLASSIFIED source: the first moves it to REVIEW_REQUIRED and
    records that; the second finds it there and records nothing (the transition table is unchanged)."""
    sid = _source(client, "sequential")
    for key in ("SEQ-1", "SEQ-2"):
        r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=_requirement_body(sid, f"SLC-{TAG}-{key}"))
        assert r.status_code == 201, r.text
    assert _source_state(sid) == "REVIEW_REQUIRED" and _versions(sid) == 2
    assert _lifecycle_records(sid) == [CLASSIFIED, PROPOSED]


def test_existing_transitions_and_refusals_are_unchanged(client, world):
    """An INGESTED source grounds no requirement (409 SOURCE_NOT_CLASSIFIED); classification moves it; a source already
    in review cannot be classified again (409 CONTENT_TRANSITION_NOT_ALLOWED); a repeated version is refused (409
    DUPLICATE_REQUIREMENT_VERSION). Refusals write nothing."""
    sid = _source(client, "states", classify=False)
    body = _requirement_body(sid, f"SLC-{TAG}-STATES")
    before = db_fingerprint()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=body)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SOURCE_NOT_CLASSIFIED"
    assert db_fingerprint() == before
    assert client.post(f"/api/catalog/sources/{sid}/classify", headers=H(AUTHOR),
                       json={"instrument_identifier": INSTR}).status_code == 200
    assert client.post("/api/catalog/requirements", headers=H(AUTHOR), json=body).status_code == 201
    before = db_fingerprint()
    again = client.post(f"/api/catalog/sources/{sid}/classify", headers=H(AUTHOR), json={"instrument_identifier": INSTR})
    assert again.status_code == 409 and again.json()["detail"]["error"] == "CONTENT_TRANSITION_NOT_ALLOWED"
    dup = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=body)
    assert dup.status_code == 409 and dup.json()["detail"]["error"] == "DUPLICATE_REQUIREMENT_VERSION"
    assert db_fingerprint() == before
    assert _lifecycle_records(sid) == [CLASSIFIED, PROPOSED]


def test_a_creation_that_fails_after_the_move_leaves_the_source_and_its_history_unchanged(client, world, monkeypatch):
    """Fault injection after the source was moved and before the creation completed: the transaction rolls back as a
    whole, so the source is still CLASSIFIED, no version exists and no transition is recorded; the same request then
    succeeds once."""
    from app.engines import catalog
    sid = _source(client, "rollback")
    real = catalog.audit

    def failing(db, **kw):
        if kw.get("action") == "requirement.create":
            raise RuntimeError("injected failure after the source transition")
        return real(db, **kw)
    monkeypatch.setattr(catalog, "audit", failing)
    with pytest.raises(RuntimeError, match="injected failure"):
        client.post("/api/catalog/requirements", headers=H(AUTHOR), json=_requirement_body(sid, f"SLC-{TAG}-RB"))
    monkeypatch.undo()
    assert (_source_state(sid), _versions(sid), _lifecycle_records(sid)) == ("CLASSIFIED", 0, [CLASSIFIED])
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=_requirement_body(sid, f"SLC-{TAG}-RB"))
    assert r.status_code == 201, r.text
    assert (_source_state(sid), _versions(sid), _lifecycle_records(sid)) == ("REVIEW_REQUIRED", 1, [CLASSIFIED, PROPOSED])


def test_an_organisation_principal_cannot_create_classify_or_extract(client, world):
    """Source lifecycle operations are Zoiko content operations: an organisation's CCO is refused (403) on every route
    that moves a source, and nothing is written."""
    sid = _source(client, "tenant", classify=False)
    before = db_fingerprint()
    attempts = [
        client.post(f"/api/catalog/sources/{sid}/classify", headers=H(CCO), json={"instrument_identifier": INSTR}),
        client.post("/api/catalog/requirements", headers=H(CCO), json=_requirement_body(sid, f"SLC-{TAG}-ORG")),
        client.post(f"/api/catalog/sources/{sid}/extract", headers=H(CCO), json={
            "instrument_identifier": INSTR, "requirement_key": f"SLC-{TAG}-ORGX", "requirement_title": "Org",
            "locator": "Art. 9", "valid_from": "2025-01-01"})]
    assert [a.status_code for a in attempts] == [403, 403, 403], [a.text for a in attempts]
    assert db_fingerprint() == before and _source_state(sid) == "INGESTED"


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and locks; exact reason required by the CI gate
    return True


def _call(route, *args, actor=AUTHOR):
    """Run a catalogue route function in its own session as a content principal: ("ok", result) or (status, detail);
    any other failure (a database deadlock) is returned as ("error", its class name)."""
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.security import Ctx
    try:
        with SessionLocal() as db:
            set_principal(db, None, actor)
            return "ok", route(*args, Ctx(db=db, actor=db.get(Actor, actor)))
    except HTTPException as e:
        return e.status_code, e.detail
    except Exception as e:  # noqa: BLE001 - the defect surfaced as a database error
        return "error", type(e).__name__


def _race(first, second, hold_after: str, monkeypatch):
    """Hold the thread named "first" right after it has run catalog.<hold_after> (inside its transaction), start
    "second", and report whether "second" was still waiting 2 seconds later. Returns ({name: outcome}, waited)."""
    from app.engines import catalog
    held, release, out = threading.Event(), threading.Event(), {}
    real = getattr(catalog, hold_after)

    def holding(*a, **k):
        r = real(*a, **k)
        if threading.current_thread().name == "first" and not held.is_set():
            held.set()
            assert release.wait(30)
        return r
    monkeypatch.setattr(catalog, hold_after, holding)
    a = threading.Thread(target=lambda: out.__setitem__("first", first()), name="first")
    a.start()
    assert held.wait(60), out
    b = threading.Thread(target=lambda: out.__setitem__("second", second()), name="second")
    b.start()
    b.join(2.0)
    waited = b.is_alive()
    release.set()
    a.join(60)
    b.join(60)
    return out, waited


@pytest.mark.parametrize("second_instrument", [INSTR, INSTR2], ids=["same-instrument", "another-instrument"])
def test_concurrent_creations_from_one_source_record_one_transition(client, world, pg, monkeypatch, second_instrument):
    """The first creation is held after it moved the source (before it commits). A second creation from the same source
    waits for it, then finds the source already in review: one recorded transition, both versions created. Before, the
    second one had read the source before the first committed and recorded "CLASSIFIED -> REVIEW_REQUIRED" a second
    time."""
    from app.routers.catalog import RequirementIn, create_requirement
    sid = _source(client, f"race-{second_instrument[-4:]}")
    first = RequirementIn(**_requirement_body(sid, f"SLC-{TAG}-R1-{second_instrument[-4:]}"))
    second = RequirementIn(**_requirement_body(sid, f"SLC-{TAG}-R2-{second_instrument[-4:]}", second_instrument))
    out, waited = _race(lambda: _call(create_requirement, first), lambda: _call(create_requirement, second),
                        "version_number", monkeypatch)
    assert waited and out["first"][0] == "ok" and out["second"][0] == "ok", out
    assert (_source_state(sid), _versions(sid)) == ("REVIEW_REQUIRED", 2)
    assert _lifecycle_records(sid) == [CLASSIFIED, PROPOSED]


def test_classification_racing_a_creation_waits_and_never_deadlocks(client, world, pg, monkeypatch):
    """A creation is held after it took the instrument lock; a classification of the same source under the same
    instrument starts meanwhile. With one lock order (source, then instrument) the classification waits, then finds the
    source in review and is refused by the transition table (409). Before, the creation locked the instrument first and
    the source last while classification locks them the other way round: PostgreSQL aborted one of them as a deadlock."""
    from app.routers.catalog import ClassifyIn, RequirementIn, classify_source, create_requirement
    sid = _source(client, "deadlock")
    body = RequirementIn(**_requirement_body(sid, f"SLC-{TAG}-DL"))
    out, waited = _race(lambda: _call(create_requirement, body),
                        lambda: _call(classify_source, sid, ClassifyIn(instrument_identifier=INSTR)),
                        "lock_instrument", monkeypatch)
    assert waited and out["first"][0] == "ok", out
    assert out["second"][0] == 409 and out["second"][1]["error"] == "CONTENT_TRANSITION_NOT_ALLOWED", out
    assert (out["second"][1]["from"], out["second"][1]["to"]) == ("REVIEW_REQUIRED", "CLASSIFIED")
    assert (_source_state(sid), _versions(sid), _lifecycle_records(sid)) == ("REVIEW_REQUIRED", 1, [CLASSIFIED, PROPOSED])


def test_an_extraction_racing_a_creation_records_one_transition(client, world, pg, monkeypatch):
    """The extractor proposal path (POST /catalog/sources/{id}/extract) creates a version from the source as well and
    is serialised the same way."""
    from app.routers.catalog import ExtractIn, RequirementIn, create_requirement, extract
    sid = _source(client, "extract")
    body = RequirementIn(**_requirement_body(sid, f"SLC-{TAG}-EX1"))
    proposal = ExtractIn(instrument_identifier=INSTR, requirement_key=f"SLC-{TAG}-EX2", requirement_title="Proposed",
                         locator="Art. 2", valid_from="2025-01-01")
    out, waited = _race(lambda: _call(create_requirement, body), lambda: _call(extract, sid, proposal),
                        "version_number", monkeypatch)
    assert waited and out["first"][0] == "ok" and out["second"][0] == "ok", out
    assert (_source_state(sid), _versions(sid), _lifecycle_records(sid)) == ("REVIEW_REQUIRED", 2, [CLASSIFIED, PROPOSED])
