"""Idempotency persistence and replay integrity for determination attestation and challenge (engines/idempotency.py).

Persistence: platform_idempotency_record is unique on (tenant, operation, key), tenant-isolated by row-level security,
written in the action's own transaction; concurrent duplicates serialise on an advisory lock over the same scope.
Replay: the committed result, not the records as they are now. An attestation's determination and obligation revisions
were current when it committed (record_to_seq null); a later revision closes them in record time, which the replay used to
report as part of the original response. Challenges and attestations are not changed after commit.

What the storage cannot give: the response itself is not stored (result_ref only), and on PostgreSQL nothing stops an
UPDATE of a determination, obligation or challenge row or of the key record; the replay is the committed result only as
long as those rows are not altered and the serialisation is unchanged. Exact replay independent of current rows needs an
immutable response snapshot (a schema change, not made here). The key stays optional. Runs on SQLite and PostgreSQL 17;
concurrency on PostgreSQL only.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"IRI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-iria-{TAG.lower()}", f"tenant-irib-{TAG.lower()}"
CCO, CCO_B = f"iri-cco-{TAG.lower()}", f"irib-cco-{TAG.lower()}"
CONTROL = f"CTRL-{TAG}-IRI"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
BASIS = "Board delegation IRI"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


# ------------------------------------------------------------------------------------------------ pure
def test_request_hashes_are_deterministic_and_bind_every_material_field():
    from app.engines.idempotency import request_hash
    base = {"determination_lineage_id": "l1", "decision": "ADOPT", "authority_basis": "Board", "reason": None,
            "revision_id": None, "evaluation_id": None}
    assert request_hash(base) == request_hash(dict(reversed(list(base.items())))) == request_hash(dict(base))
    changed = {k: request_hash({**base, k: v}) for k, v in (("determination_lineage_id", "l2"), ("decision", "REJECT"),
                                                          ("authority_basis", "Other"), ("reason", "r"),
                                                          ("revision_id", "x"), ("evaluation_id", "y"))}
    assert len(set(changed.values()) | {request_hash(base)}) == 7  # every field is material
    assert request_hash({**base, "authority_basis": "Délégation"}) == request_hash({**base, "authority_basis": "Délégation"})


# ------------------------------------------------------------------------------------------------ world
def _ast(t: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{t}_{nid}", "operator": "EQ", "source_mappings": [f"sm_{t}_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{t}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{t}_{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        pred("eu", EU, "BOOLEAN", True), pred("kind", KIND, "ENUM", "CREDIT_INSTITUTION")]}


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import ControlDefinition, Instrument, ObligationDefinition, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Replay Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=CONTROL, title="Replay control", objective="Probe.", category="RISK_MANAGEMENT"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="IRI CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="IRI CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Replay Act",
        "locator": f"OJ L {TAG}/iri", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"IRI-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code == 201, rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"IRI_{TAG}", title="Replay obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True,
                                 satisfaction_template={"node_type": "CONTROL", "node_id": "c", "control_key": CONTROL})
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    t, p = secrets.token_hex(3), rv["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}

    def vec(vid, cls, eu, kind, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {EU: eu, KIND: kind}, "expected_outcome": out}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(t), "valid_from": "2025-01-01",
        "change_class": "EXECUTABLE_OUTCOME",
        "golden_vectors": [vec("P", "POSITIVE", True, "CREDIT_INSTITUTION", "APPLIES"),
                           vec("N", "NEGATIVE", False, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
                           vec("U", "UNKNOWN", True, {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")],
        "source_mappings": [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"iri-{TAG}", "changelog": "iri"})
    assert r.status_code == 200, r.text
    return True


def _proposed(name, tenant=TA, actor=CCO) -> dict:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=tenant, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    for key, value in ((EU, True), (KIND, "CREDIT_INSTITUTION")):
        _fact(sid, key, value, actor=actor)
    ev = S["client"].post("/api/applicability/evaluations", headers=H(actor),
                          json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert ev.status_code == 200 and ev.json()["current_determination"]["state"] == "PROPOSED", ev.text
    return {"sid": sid, "lineage": ev.json()["current_determination"]["lineage_id"], "actor": actor}


def _fact(sid, key, value, *, actor=CCO, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=1 if change else 40)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "Changed"}
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(actor), json=body)
    assert r.status_code == 200, r.text


def _flag(x, kind):
    """A newer evaluation with another result flags the adopted determination; returns its current revision ID."""
    _fact(x["sid"], KIND, kind, actor=x["actor"], change=True)
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        [cur] = db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.lineage_id == x["lineage"],
            ApplicabilityDetermination.record_to_seq.is_(None))).scalars().all()
        assert cur.needs_review, cur.state
        return cur.revision_id


def _post(lineage, path, body, key=None, actor=CCO):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/{path}",
                            headers={**H(actor), **({"Idempotency-Key": key} if key else {})}, json=body)


def _attest(lineage, key=None, actor=CCO, **extra):
    return _post(lineage, "attest", {"decision": "ADOPT", "authority_basis": BASIS, **extra}, key, actor)


def _challenge(lineage, key=None, actor=CCO, detail="The entity type is wrong"):
    return _post(lineage, "challenge", {"challenge_class": "FACT_WRONG", "detail": detail}, key, actor)


def _keys(key, tenant=None) -> int:
    from app.db import admin_engine
    q = "SELECT count(*) FROM platform_idempotency_record WHERE idempotency_key = :k" + (" AND tenant_id = :t" if tenant else "")
    with admin_engine.connect() as c:
        return c.execute(text(q), {"k": key, "t": tenant}).scalar()


def _writes(lineage) -> tuple:
    from app.db import admin_engine
    with admin_engine.connect() as c:
        def one(sql):
            return c.execute(text(sql), {"l": lineage}).scalar()
        return (one("SELECT count(*) FROM governance_attestation a JOIN applicability_determination_revision d "
                    "ON d.revision_id = a.target_revision_id WHERE d.lineage_id = :l"),
                one("SELECT count(*) FROM applicability_determination_challenge WHERE determination_lineage_id = :l"),
                one("SELECT count(*) FROM platform_audit_event WHERE object_id = :l"),
                one("SELECT count(*) FROM platform_outbox_event WHERE aggregate_id = :l"))


# ------------------------------------------------------------------------------------------------ replay integrity
def test_replay_after_a_later_obligation_revision_returns_the_original_result(client, world):
    """The attestation's determination and obligation revisions are superseded by a re-adoption (ADOPTED -> ADOPTED);
    the replay still answers what was committed (both current, record_to_seq null) and writes nothing."""
    x = _proposed("replay-revision")
    key = f"rev-{secrets.token_hex(8)}"
    original = _attest(x["lineage"], key)
    assert original.status_code == 200, original.text
    from app.db import AdminSession
    from app.engines.facts import assert_fact
    from app.models import FactDefinition, Subject
    with AdminSession() as db:  # an UNCERTAIN evaluation flags the adoption; the facts come back; it is re-adopted
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == KIND)).scalars().one()
        assert_fact(db, tenant_id=TA, subject=db.get(Subject, x["sid"]), fd=fd, value=None, valid_from=NOW() - timedelta(days=2),
                    valid_to=None, provenance_class="P3_CUSTOMER_ATTESTED", source_ref=None, actor_id=CCO,
                    change_kind="WITHDRAWAL", reason="Pending")
        db.commit()
    assert client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": x["sid"], "obligation_definition_id": S["odef"]}).json()["outcome"] == "UNCERTAIN"
    _fact(x["sid"], KIND, "CREDIT_INSTITUTION", change=True)
    assert _attest(x["lineage"]).status_code == 200  # a new obligation revision supersedes the original one
    before = db_fingerprint()
    replay = _attest(x["lineage"], key)
    assert replay.status_code == 200 and replay.json() == original.json(), (replay.json(), original.json())
    assert db_fingerprint() == before


def test_replay_after_closure_and_reopening_returns_the_original_result(client, world):
    x = _proposed("replay-close")
    key = f"close-{secrets.token_hex(8)}"
    original = _attest(x["lineage"], key)
    assert original.status_code == 200, original.text
    assert _attest(x["lineage"], revision_id=_flag(x, "PAYMENT_INSTITUTION")).status_code == 200   # CLOSED
    assert _attest(x["lineage"], revision_id=_flag(x, "CREDIT_INSTITUTION")).status_code == 200    # ADOPTED again
    before = db_fingerprint()
    replay = _attest(x["lineage"], key)
    assert replay.status_code == 200 and replay.json() == original.json(), (replay.json(), original.json())
    assert db_fingerprint() == before
    # A closing attestation's replay has no obligation, as its original response.
    y = _proposed("replay-closing")
    assert _attest(y["lineage"]).status_code == 200
    ckey = f"closing-{secrets.token_hex(8)}"
    closing = _attest(y["lineage"], ckey, revision_id=_flag(y, "PAYMENT_INSTITUTION"))
    assert closing.status_code == 200 and closing.json()["obligation"] is None
    assert _attest(y["lineage"], revision_id=_flag(y, "CREDIT_INSTITUTION")).status_code == 200
    assert _attest(y["lineage"], ckey, revision_id=closing.json()["attestation"]["target_revision_id"]).json() == closing.json()


def test_challenge_replays_are_unaffected_by_later_challenges_and_attestation(client, world):
    x = _proposed("replay-challenge")
    key = f"ch-{secrets.token_hex(8)}"
    original = _challenge(x["lineage"], key)  # PROPOSED -> IN_REVIEW
    assert original.status_code == 200, original.text
    assert _challenge(x["lineage"], detail="Another point").status_code == 200   # another challenge
    assert _attest(x["lineage"]).status_code == 200                              # then adopted
    before, writes = db_fingerprint(), _writes(x["lineage"])
    replay = _challenge(x["lineage"], key)
    assert replay.status_code == 200 and replay.json() == original.json()
    assert db_fingerprint() == before and _writes(x["lineage"]) == writes  # no second IN_REVIEW, record or event


def test_a_lost_response_is_recovered_as_committed_even_after_later_changes(client, world, monkeypatch):
    from app.routers import applicability as router
    x = _proposed("replay-lost")
    key = f"lost-{secrets.token_hex(8)}"
    real, calls = router.to_dict, []

    def fail_once(obj):
        if not calls:
            calls.append(obj)
            raise RuntimeError("simulated failure after commit, before the response")
        return real(obj)
    monkeypatch.setattr(router, "to_dict", fail_once)
    with pytest.raises(RuntimeError):
        _attest(x["lineage"], key)
    monkeypatch.undo()
    first = _attest(x["lineage"], key)  # the committed result, recovered
    assert first.status_code == 200 and first.json()["determination"]["record_to_seq"] is None
    assert _attest(x["lineage"], revision_id=_flag(x, "PAYMENT_INSTITUTION")).status_code == 200  # closed later
    assert _attest(x["lineage"], key).json() == first.json()
    assert _writes(x["lineage"])[0] == 2 and _keys(key) == 1  # the original adoption and the closure: one each


# ------------------------------------------------------------------------------------------------ persistence integrity
def test_keys_are_scoped_by_tenant_and_operation(client, world):
    key = f"scope-{secrets.token_hex(8)}"
    a = _proposed("scope-a")
    b = _proposed("scope-b", TB, CCO_B)
    ra = _attest(a["lineage"], key)
    rb = _attest(b["lineage"], key, actor=CCO_B)  # another organisation, the same key: its own attestation
    assert ra.status_code == rb.status_code == 200
    assert ra.json()["attestation"]["attestation_id"] != rb.json()["attestation"]["attestation_id"]
    assert (_keys(key, TA), _keys(key, TB)) == (1, 1)
    before = db_fingerprint()
    # Never a replay of another organisation's result: B's key names B's request, so A's determination is a conflict.
    leak = _attest(a["lineage"], key, actor=CCO_B)
    assert leak.status_code == 409 and leak.json()["detail"]["error"] == "idempotency_conflict", leak.text
    assert "attestation" not in leak.text
    assert db_fingerprint() == before
    # Another operation is another scope, in both directions.
    c = _proposed("scope-c")
    ck = f"scope-op-{secrets.token_hex(8)}"
    assert _challenge(c["lineage"], ck).status_code == 200
    adopted = _attest(c["lineage"], ck)
    assert adopted.status_code == 200 and adopted.json()["determination"]["state"] == "ADOPTED"
    assert _challenge(a["lineage"], key).status_code == 200  # the attest key, used for a challenge
    assert _keys(ck) == 2 and _keys(key, TA) == 2


def test_a_database_error_before_commit_leaves_no_key_and_the_retry_commits_once(client, world, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.engines import idempotency
    x = _proposed("db-failure")
    key = f"dbfail-{secrets.token_hex(8)}"
    real = idempotency.remember

    def remember_then_fail(*a, **k):
        real(*a, **k)  # the key row is written (flushed) in the transaction, then the database fails
        raise OperationalError("COMMIT", {}, Exception("simulated database failure before commit"))
    monkeypatch.setattr(idempotency, "remember", remember_then_fail)
    before = db_fingerprint()
    with pytest.raises(OperationalError):
        _attest(x["lineage"], key)
    assert db_fingerprint() == before and _keys(key) == 0  # no orphaned key, attestation, audit row or event
    monkeypatch.undo()
    first, again = _attest(x["lineage"], key), _attest(x["lineage"], key)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert _writes(x["lineage"])[0] == 1 and _keys(key) == 1


def test_an_error_in_the_actions_own_rows_is_never_reported_as_an_idempotency_conflict(client, world, monkeypatch):
    """The key's insert is flushed with the action's pending audit and outbox rows; any integrity error among them was
    caught as the key's own and answered 409 idempotency_conflict (only when a key was sent). It is the action's error
    and propagates as without a key; nothing is written; the retry commits once."""
    from sqlalchemy.exc import IntegrityError

    from app.engines import applicability as appl
    x = _proposed("own-row-error")
    key = f"own-{secrets.token_hex(8)}"
    real = appl.emit

    def broken_event(*a, **k):
        ev = real(*a, **k)
        if k.get("event_type") == "determination.adopted":
            ev.event_type = None  # NOT NULL: the action's own row fails at the next flush
        return ev
    monkeypatch.setattr(appl, "emit", broken_event)
    before = db_fingerprint()
    with pytest.raises(IntegrityError):
        _attest(x["lineage"], key)
    with pytest.raises(IntegrityError):
        _attest(x["lineage"])  # the same failure without a key, for comparison
    assert db_fingerprint() == before and _keys(key) == 0
    monkeypatch.undo()
    assert _attest(x["lineage"], key).status_code == 200 and _writes(x["lineage"])[0] == 1


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # advisory locks and concurrent transactions; reason required by the CI gate
    return True


def _engine_attest(lineage, key, tenant=TA, actor=CCO, basis=BASIS):
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.models import Actor
    from app.security import Ctx
    with SessionLocal() as db:
        set_principal(db, tenant, actor)
        r = appl.attest(db, ctx=Ctx(db=db, actor=db.get(Actor, actor)), determination_lineage_id=lineage,
                        decision="ADOPT", authority_basis=basis, idempotency_key=key)
        db.commit()
        return r["attestation"].attestation_id


def _race(monkeypatch, first, second):
    """`first` runs in thread A and is held inside _activate_obligation (after the key lock, the decision lock and the
    record sequence); `second` runs in thread B. Returns both results and whether B was still waiting while A was held."""
    from app.engines import applicability as appl
    held, release = threading.Event(), threading.Event()
    real = appl._activate_obligation

    def holding(*a, **k):
        if threading.current_thread().name == "A":
            held.set()
            assert release.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(appl, "_activate_obligation", holding)
    out = {}

    def run(name, fn):
        try:
            out[name] = ("ok", fn())
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001 - reported by the assertions
            out[name] = ("error", repr(e))
    a = threading.Thread(target=run, args=("A", first), name="A")
    a.start()
    assert held.wait(30), "thread A never reached the critical section"
    b = threading.Thread(target=run, args=("B", second), name="B")
    b.start()
    b.join(1.5)
    waited = b.is_alive()
    release.set()
    a.join(60)
    b.join(60)
    return out, waited


def test_concurrent_conflicting_requests_with_one_key_commit_one_and_refuse_the_other(client, world, pg, monkeypatch):
    x = _proposed("race-conflict")
    key = f"race-{secrets.token_hex(8)}"
    out, waited = _race(monkeypatch, lambda: _engine_attest(x["lineage"], key),
                        lambda: _engine_attest(x["lineage"], key, basis="Another delegation"))
    assert waited and out["A"][0] == "ok", out
    assert out["B"][0] == 409 and out["B"][1]["error"] == "idempotency_conflict", out  # decided on the key, after A
    assert _writes(x["lineage"])[0] == 1 and _keys(key) == 1


def test_one_key_in_two_organisations_concurrently_is_two_independent_requests(client, world, pg, monkeypatch):
    key = f"race-tenants-{secrets.token_hex(8)}"
    a = _proposed("race-tenant-a")
    b = _proposed("race-tenant-b", TB, CCO_B)
    out, _ = _race(monkeypatch, lambda: _engine_attest(a["lineage"], key),
                   lambda: _engine_attest(b["lineage"], key, TB, CCO_B))
    assert out["A"][0] == out["B"][0] == "ok" and out["A"][1] != out["B"][1], out
    assert (_keys(key, TA), _keys(key, TB)) == (1, 1)
