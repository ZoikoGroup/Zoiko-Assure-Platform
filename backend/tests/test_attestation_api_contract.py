"""Determination attestation and challenge API contract: every refusal's status and shape, and retries.

Refusals are pinned as they are. The coded ones carry codes this API introduced earlier (DETERMINATION_TRANSITION_NOT_
PERMITTED, STALE_DETERMINATION_REVISION, STALE_EVALUATION, CHALLENGE_CLASS_INVALID, CHALLENGE_DETAIL_REQUIRED,
IDEMPOTENCY_KEY_INVALID); the uncoded ones (newer evaluation, UNCERTAIN, missing reason or authority basis, unknown
lineage) have no code in ZA-APP-001 §12 or ZA-API-001, so none is invented here: a change of either kind is an API-owner
decision and must update these tests.

Retries (ZA-API-001 §08, API-REQ-004/005): both actions take an optional Idempotency-Key with the semantics POST /test-runs
established (engines/idempotency.py). A keyed retry of a committed request returns its result and writes nothing; the key
with another request is 409 idempotency_conflict; a request that did not commit leaves no key behind; concurrent
duplicates wait for the first. Without a key: a repeated attestation is refused (409, nothing written) and a repeated
challenge is a new challenge (APP-REQ-011). Keys are not required yet (API-REQ-004 "MUST"), so this is not idempotency
compliance. Runs on SQLite and PostgreSQL 17; concurrent duplicates on PostgreSQL only.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"ATC-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-atc-{TAG.lower()}", f"atc-cco-{TAG.lower()}"
CONTROL = f"CTRL-{TAG}-ATC"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
BASIS = "Board delegation ATC"
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


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
        db.add(Instrument(identifier=INSTR, title="Attestation Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(ControlDefinition(key=CONTROL, title="Attestation control", objective="Probe.", category="RISK_MANAGEMENT"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="ATC CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Attestation Act",
        "locator": f"OJ L {TAG}/atc", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"ATC-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code == 201, rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"ATC_{TAG}", title="Attestation obligation", instrument_id=inst.instrument_id,
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
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"atc-{TAG}", "changelog": "atc"})
    assert r.status_code == 200, r.text
    return True


def _proposed(name) -> dict:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    for key, value in ((EU, True), (KIND, "CREDIT_INSTITUTION")):
        assert S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=40)).isoformat()}).status_code == 200
    ev = _evaluate(sid)
    assert ev["current_determination"]["state"] == "PROPOSED"
    return {"sid": sid, "ev": ev, "lineage": ev["current_determination"]["lineage_id"]}


def _evaluate(sid):
    r = S["client"].post("/api/applicability/evaluations", headers=H(CCO),
                         json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert r.status_code == 200, r.text
    return r.json()


def _uncertain(sid):
    """Withdraw the entity type: the next evaluation is UNCERTAIN (newer than the determination's evaluation)."""
    from app.db import AdminSession
    from app.engines.facts import assert_fact
    from app.models import FactDefinition, Subject
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == KIND)).scalars().one()
        assert_fact(db, tenant_id=TA, subject=db.get(Subject, sid), fd=fd, value=None, valid_from=NOW() - timedelta(days=1),
                    valid_to=None, provenance_class="P3_CUSTOMER_ATTESTED", source_ref=None, actor_id=CCO,
                    change_kind="WITHDRAWAL", reason="Pending confirmation")
        db.commit()
    assert _evaluate(sid)["outcome"] == "UNCERTAIN"


def _post(lineage, path, body, key=None):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/{path}",
                            headers={**H(CCO), **({"Idempotency-Key": key} if key else {})}, json=body)


def _attest(lineage, decision="ADOPT", key=None, **extra):
    return _post(lineage, "attest", {"decision": decision, "authority_basis": BASIS, **extra}, key)


def _challenge(lineage, key=None, cls="FACT_WRONG", detail="The entity type is wrong"):
    return _post(lineage, "challenge", {"challenge_class": cls, "detail": detail}, key)


def _counts(lineage, key=None, obligation_lineage=None) -> dict:
    """Every record the two actions write for one determination lineage."""
    from app.db import admin_engine

    def one(sql, **p):
        with admin_engine.connect() as c:
            return c.execute(text(sql), p).scalar()
    out = {
        "attestations": one("SELECT count(*) FROM governance_attestation a JOIN applicability_determination_revision d "
                            "ON d.revision_id = a.target_revision_id WHERE d.lineage_id = :l", l=lineage),
        "challenges": one("SELECT count(*) FROM applicability_determination_challenge WHERE determination_lineage_id = :l",
                          l=lineage),
        "attest_audits": one("SELECT count(*) FROM platform_audit_event WHERE object_id = :l AND action = 'determination.attest'",
                             l=lineage),
        "challenge_audits": one("SELECT count(*) FROM platform_audit_event WHERE object_id = :l "
                                "AND action = 'determination.challenge'", l=lineage),
        "events": {t: one("SELECT count(*) FROM platform_outbox_event WHERE aggregate_id = :l AND event_type = :t", l=lineage, t=t)
                   for t in ("determination.adopted", "determination.rejected", "determination.challenged")},
    }
    if obligation_lineage:
        out["obligation_events"] = {t: one("SELECT count(*) FROM platform_outbox_event WHERE aggregate_id = :l AND "
                                           "event_type = :t", l=obligation_lineage, t=t) for t in ("obligation.adopted",
                                                                                                   "obligation.revised")}
    if key:
        out["keys"] = one("SELECT count(*) FROM platform_idempotency_record WHERE idempotency_key = :k", k=key)
    return out


# ------------------------------------------------------------------------------------------------ refusals
def test_every_attestation_and_challenge_refusal_keeps_its_status_and_shape(client, world):
    p = _proposed("shape-p")["lineage"]
    r = _proposed("shape-r")["lineage"]
    assert _challenge(r).status_code == 200                                    # IN_REVIEW
    j = _proposed("shape-j")["lineage"]
    assert _challenge(j).status_code == 200 and _attest(j, "REJECT", reason="Declined").status_code == 200  # REJECTED
    a = _proposed("shape-a")["lineage"]
    assert _attest(a).status_code == 200                                      # ADOPTED
    n = _proposed("shape-n")
    _uncertain(n["sid"])                                                       # PROPOSED, a newer UNCERTAIN evaluation
    u = _proposed("shape-u")
    assert _attest(u["lineage"]).status_code == 200
    _uncertain(u["sid"])                                                       # ADOPTED, flagged by an UNCERTAIN one

    def coded(status, code, *keys):
        def check(x):
            d = x.json().get("detail")
            return x.status_code == status and isinstance(d, dict) and d.get("error") == code and all(k in d for k in keys)
        return check

    def sentence(status, words):
        return lambda x: x.status_code == status and x.json().get("detail") == words

    def validation(field):
        return lambda x: x.status_code == 422 and isinstance(x.json()["detail"], list) and \
            any(field in e["loc"] for e in x.json()["detail"])
    transition = ("from_state", "decision", "message")
    cases = [
        # coded refusals (codes this API introduced; none is in ZA-APP-001 §12 or ZA-API-001)
        ((p, "attest", {"decision": "REJECT", "authority_basis": BASIS, "reason": "No"}), coded(409, "DETERMINATION_TRANSITION_NOT_PERMITTED", *transition)),
        ((a, "attest", {"decision": "ADOPT", "authority_basis": BASIS}), coded(409, "DETERMINATION_TRANSITION_NOT_PERMITTED", *transition)),
        ((j, "attest", {"decision": "ADOPT", "authority_basis": BASIS}), coded(409, "DETERMINATION_TRANSITION_NOT_PERMITTED", *transition)),
        ((p, "attest", {"decision": "ADOPT", "authority_basis": BASIS, "revision_id": "not-the-current-revision"}),
         coded(409, "STALE_DETERMINATION_REVISION", "current_revision_id", "message")),
        ((p, "attest", {"decision": "ADOPT", "authority_basis": BASIS, "evaluation_id": "not-the-evaluation"}),
         coded(409, "STALE_EVALUATION", "evaluation_id", "message")),
        ((p, "challenge", {"challenge_class": "NOPE", "detail": "x"}), coded(422, "CHALLENGE_CLASS_INVALID", "allowed")),
        ((p, "challenge", {"challenge_class": "FACT_WRONG", "detail": "   "}), coded(422, "CHALLENGE_DETAIL_REQUIRED", "message")),
        # uncoded refusals: a sentence as `detail` (no approved code exists)
        ((n["lineage"], "attest", {"decision": "ADOPT", "authority_basis": BASIS}),
         sentence(409, "A newer evaluation exists; re-review before attesting")),
        ((u["lineage"], "attest", {"decision": "ADOPT", "authority_basis": BASIS}),
         sentence(409, "UNCERTAIN cannot be adopted (DOM-INV-03); resolve facts first")),
        ((r, "attest", {"decision": "REJECT", "authority_basis": BASIS}), sentence(422, "Rejection reason is mandatory")),
        ((r, "attest", {"decision": "REJECT", "authority_basis": BASIS, "reason": "  "}), sentence(422, "Rejection reason is mandatory")),
        ((p, "attest", {"decision": "ADOPT", "authority_basis": "   "}),
         sentence(422, "authority_basis is required (APP §13: named actor, authority basis, exact revision)")),
        ((p, "attest", {"decision": "REVOKE", "authority_basis": BASIS}), sentence(422, "decision must be ADOPT or REJECT")),
        (("no-such-lineage", "attest", {"decision": "ADOPT", "authority_basis": BASIS}), sentence(404, "Determination not found")),
        (("no-such-lineage", "challenge", {"challenge_class": "FACT_WRONG", "detail": "x"}), sentence(404, "Not Found")),
        # request validation (FastAPI's list form)
        ((p, "attest", {"authority_basis": BASIS}), validation("decision")),
        ((p, "attest", {"decision": "ADOPT", "authority_basis": "x" * 201}), validation("authority_basis")),
        ((p, "attest", {"decision": "ADOPT", "authority_basis": BASIS, "valid_from": "2027-01-01"}), validation("valid_from")),
        ((p, "challenge", {"challenge_class": "FACT_WRONG"}), validation("detail")),
    ]
    before = db_fingerprint()
    failures = [(c, x.status_code, x.text[:200]) for c, check in cases if not check(x := _post(*c))]
    assert not failures, failures
    for path, body in (("attest", {"decision": "ADOPT", "authority_basis": BASIS}),
                       ("challenge", {"challenge_class": "FACT_WRONG", "detail": "x"})):
        bad_key = _post(p, path, body, key="k" * 201)
        assert coded(422, "IDEMPOTENCY_KEY_INVALID", "message")(bad_key), bad_key.text
    assert db_fingerprint() == before  # no refusal writes anything (no audit row, no outbox event, no key)


# ------------------------------------------------------------------------------------------------ retries
def test_a_keyed_attestation_retried_after_its_commit_returns_the_same_result_and_writes_once(client, world):
    x = _proposed("retry-adopt")
    key = f"adopt-{secrets.token_hex(8)}"
    first = _attest(x["lineage"], key=key)
    assert first.status_code == 200, first.text
    obligation = first.json()["obligation"]["lineage_id"]
    once = _counts(x["lineage"], key, obligation)
    assert once == {"attestations": 1, "challenges": 0, "attest_audits": 1, "challenge_audits": 0,
                    "events": {"determination.adopted": 1, "determination.rejected": 0, "determination.challenged": 0},
                    "obligation_events": {"obligation.adopted": 1, "obligation.revised": 0}, "keys": 1}
    other = _proposed("retry-other")["lineage"]  # another determination, for the cross-lineage key reuse below
    before = db_fingerprint()
    for body_extra in ({}, {"authority_basis": f"  {BASIS}  "}):  # the request as the action reads it (trimmed)
        again = _post(x["lineage"], "attest", {"decision": "ADOPT", "authority_basis": BASIS, **body_extra}, key)
        assert again.status_code == 200, again.text
        for part, ident in (("attestation", "attestation_id"), ("determination", "revision_id"), ("obligation", "revision_id")):
            assert again.json()[part][ident] == first.json()[part][ident], part
    # The key with another request (another authority basis, or another determination) is refused and changes nothing.
    clash =[_attest(x["lineage"], key=key, authority_basis="Another delegation"), _attest(other, key=key)]
    assert all(c.status_code == 409 and c.json()["detail"]["error"] == "idempotency_conflict" for c in clash), [c.text for c in clash]
    # Without a key a repeat is refused, as before.
    assert _attest(x["lineage"]).json()["detail"]["error"] == "DETERMINATION_TRANSITION_NOT_PERMITTED"
    assert db_fingerprint() == before and _counts(x["lineage"], key, obligation) == once
    # A key is scoped to its operation: the same key on a challenge is a challenge of its own.
    assert _challenge(x["lineage"], key=key).status_code == 200
    # A keyed rejection replays too (no obligation, as the original response).
    y = _proposed("retry-reject")
    assert _challenge(y["lineage"]).status_code == 200
    rkey = f"reject-{secrets.token_hex(8)}"
    rejected = [_attest(y["lineage"], "REJECT", key=rkey, reason="Declined") for _ in range(2)]
    assert [z.status_code for z in rejected] == [200, 200] and rejected[0].json() == rejected[1].json()
    assert rejected[1].json()["obligation"] is None
    assert _counts(y["lineage"], rkey)["events"]["determination.rejected"] == 1


def test_challenges_without_a_key_are_each_recorded_and_a_keyed_retry_is_recorded_once(client, world):
    x = _proposed("retry-challenge")
    # Without a key every request is a governed challenge record of its own (APP-REQ-011); no deduplication.
    plain = [_challenge(x["lineage"]) for _ in range(2)]
    assert [c.status_code for c in plain] == [200, 200] and plain[0].json()["challenge_id"] != plain[1].json()["challenge_id"]
    c2 = _counts(x["lineage"])
    assert (c2["challenges"], c2["challenge_audits"], c2["events"]["determination.challenged"]) == (2, 2, 2)
    # With a key a retry returns the recorded challenge and writes nothing.
    y = _proposed("retry-challenge-key")
    key = f"challenge-{secrets.token_hex(8)}"
    first = _challenge(y["lineage"], key=key)
    assert first.status_code == 200, first.text
    before = db_fingerprint()
    assert [_challenge(y["lineage"], key=key, detail=" The entity type is wrong ").json() for _ in range(2)] == [first.json()] * 2
    clash = _challenge(y["lineage"], key=key, detail="Something else")
    assert clash.status_code == 409 and clash.json()["detail"]["error"] == "idempotency_conflict"
    assert db_fingerprint() == before
    c = _counts(y["lineage"], key)
    assert (c["challenges"], c["challenge_audits"], c["events"]["determination.challenged"], c["keys"]) == (1, 1, 1, 1)
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:  # PROPOSED -> IN_REVIEW once, however often the request arrives
        revs = db.execute(select(ApplicabilityDetermination.state).where(
            ApplicabilityDetermination.lineage_id == y["lineage"])).scalars().all()
    assert sorted(revs) == ["IN_REVIEW", "PROPOSED"]


def test_a_failed_attestation_leaves_no_key_and_its_retry_commits_once(client, world, monkeypatch):
    from app.engines import applicability as appl
    x = _proposed("retry-failed")
    key = f"failed-{secrets.token_hex(8)}"

    def boom(*a, **k):
        raise HTTPException(503, "simulated failure after the attestation was written")
    monkeypatch.setattr(appl, "_activate_obligation", boom)
    before = db_fingerprint()
    assert _attest(x["lineage"], key=key).status_code == 503
    assert db_fingerprint() == before  # rolled back with everything else: no attestation, event, audit row or key
    monkeypatch.undo()
    ok = [_attest(x["lineage"], key=key) for _ in range(2)]
    assert [z.status_code for z in ok] == [200, 200] and ok[0].json() == ok[1].json()
    c = _counts(x["lineage"], key, ok[0].json()["obligation"]["lineage_id"])
    assert (c["attestations"], c["attest_audits"], c["events"]["determination.adopted"], c["keys"]) == (1, 1, 1, 1)
    assert c["obligation_events"]["obligation.adopted"] == 1


def test_a_response_lost_after_the_commit_is_recovered_with_the_same_key(client, world, monkeypatch):
    """ZA-API-001 §08 "5xx after unknown commit: Client retries with same key. Server resolves committed result rather
    than creating another mutation." The attestation commits; building the response then fails."""
    from app.models import AdoptionAttestation
    from app.routers import applicability as router
    x = _proposed("retry-lost")
    key = f"lost-{secrets.token_hex(8)}"
    real, calls = router.to_dict, []

    def fail_once(obj):
        if not calls:
            calls.append(obj)
            raise RuntimeError("simulated failure after commit")
        return real(obj)
    monkeypatch.setattr(router, "to_dict", fail_once)
    with pytest.raises(RuntimeError):
        _attest(x["lineage"], key=key)
    monkeypatch.undo()
    committed = _counts(x["lineage"], key)
    assert (committed["attestations"], committed["keys"]) == (1, 1)  # it did commit
    retry = _attest(x["lineage"], key=key)
    assert retry.status_code == 200, retry.text
    from app.db import AdminSession
    with AdminSession() as db:
        [att_id] = db.execute(select(AdoptionAttestation.attestation_id).where(
            AdoptionAttestation.attestation_id == retry.json()["attestation"]["attestation_id"])).scalars().all()
    assert _counts(x["lineage"], key) == committed and att_id == retry.json()["attestation"]["attestation_id"]


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # advisory locks and concurrent transactions; reason required by the CI gate
    return True


def _ctx(db):
    from app.db import set_principal
    from app.models import Actor
    from app.security import Ctx
    set_principal(db, TA, CCO)
    return Ctx(db=db, actor=db.get(Actor, CCO))


def _race(monkeypatch, target, first, second):
    """`first` runs in thread A, held inside applicability.`target`; `second` runs in thread B, which must still be
    waiting while A is held. Returns both results and whether B waited."""
    from app.engines import applicability as appl
    held, release = threading.Event(), threading.Event()
    real = getattr(appl, target)

    def holding(*a, **k):
        if threading.current_thread().name == "A":
            held.set()
            assert release.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(appl, target, holding)
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


def test_concurrent_duplicate_attestations_with_one_key_commit_once(client, world, pg, monkeypatch):
    from app.db import SessionLocal
    from app.engines import applicability as appl
    x = _proposed("race-attest-key")
    key = f"race-{secrets.token_hex(8)}"

    def attest():
        with SessionLocal() as db:
            r = appl.attest(db, ctx=_ctx(db), determination_lineage_id=x["lineage"], decision="ADOPT",
                            authority_basis=BASIS, idempotency_key=key)
            db.commit()
            return r["attestation"].attestation_id, r["determination"].revision_id
    out, waited = _race(monkeypatch, "_activate_obligation", attest, attest)
    assert waited and out["A"][0] == "ok" and out["B"] == out["A"], out  # B waited on the key, then replayed A
    c = _counts(x["lineage"], key)
    assert (c["attestations"], c["attest_audits"], c["events"]["determination.adopted"], c["keys"]) == (1, 1, 1, 1)


def test_concurrent_duplicate_challenges_with_one_key_record_once(client, world, pg, monkeypatch):
    from app.db import SessionLocal
    from app.routers.applicability import ChallengeIn, challenge
    x = _proposed("race-challenge-key")
    key = f"race-{secrets.token_hex(8)}"

    def challenged():
        with SessionLocal() as db:
            return challenge(x["lineage"], ChallengeIn(challenge_class="FACT_WRONG", detail="race"), _ctx(db),
                             idempotency_key=key)["challenge_id"]
    out, waited = _race(monkeypatch, "_new_det_revision", challenged, challenged)  # PROPOSED -> IN_REVIEW holds A
    assert waited and out["A"][0] == "ok" and out["B"] == out["A"], out
    c = _counts(x["lineage"], key)
    assert (c["challenges"], c["challenge_audits"], c["events"]["determination.challenged"], c["keys"]) == (1, 1, 1, 1)
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        n = db.execute(select(func.count()).select_from(ApplicabilityDetermination).where(
            ApplicabilityDetermination.lineage_id == x["lineage"])).scalar()
    assert n == 2  # PROPOSED, then one IN_REVIEW revision
