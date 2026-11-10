"""Rule publication holds the requirement versions and provisions it validates (ZA-RUL-001 §§08, 11, 12, 15; INV-06,
INV-09; DAT-REQ-012).

Publication may bind only VERIFIED or PUBLISHED requirement versions and provisions (catalog.RELEASABLE), through the
definition's links (release_binding) and the draft's source mappings (validate_draft_source_mappings). Under READ
COMMITTED nothing kept those rows from changing between that validation and the commit: another publication superseding
an older version of the same requirement (publish_bound), or any lifecycle change made below the API (no API path
withdraws content). catalog.lock_release_content now row-locks and re-reads them first:

  draft row -> definition row -> requirement versions (ID order) -> provisions (ID order) -> rule scope ->
  decision scopes -> record sequence

so a change either commits before the publication reads the rows (and is refused by the existing gates, with their
existing 409 RELEASE_CONTENT_NOT_VERIFIED contract) or waits until the release has committed. A withdrawal is simulated
below the API, as an owner UPDATE of the lifecycle, because the application has no withdrawal operation.
"""
from __future__ import annotations

import random
import secrets
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H
from test_authorization import db_fingerprint
from test_source_mapping_inheritance import VECTORS, pred

TAG = secrets.token_hex(3).upper()
INSTR = f"PCL-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
S: dict = {}
_n = iter(range(10**6))


# ------------------------------------------------------------------------------------------------ world
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Publication Locking Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Locking Act",
        "locator": f"OJ L {TAG}/pcl", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    S["client"] = client
    return True


def requirement(key=None) -> dict:
    """A VERIFIED requirement version with two provisions (with `key`: a later version of that requirement)."""
    client = S["client"]
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR,
        "requirement_key": key or f"PCL-{TAG}-{next(_n)}", "title": "Register", "locator": "Art. 2",
        "valid_from": "2026-01-01" if key else "2025-01-01",
        "provisions": [{"locator": f"Art. 2({i})", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST",
                        "action": "keep register", "regulated_object": "register", "timing": "continuous",
                        "normalized_meaning": f"Keep a register ({i}).", "evidence_expectation": "Register"} for i in (1, 2)]})
    assert r.status_code in (200, 201), r.text
    assert client.post(f"/api/catalog/requirements/{r.json()['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return r.json()


def definition(*rvs) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"PCL_{TAG}_{next(_n)}", title="Locking obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for rv in rvs for p in rv["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def rec(tag, mid, rv, provision=0) -> dict:
    p = rv["provisions"][provision]
    return {"source_mapping_id": f"sm_{tag}_{mid}", "requirement_version_id": rv["requirement_version_id"],
            "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
            "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT"}


def verified_draft(odef_id, cited: list[dict], *, change_class="EXECUTABLE_OUTCOME") -> tuple[str, str]:
    """A VERIFIED, shadowed draft whose root cites one mapping per requirement version in `cited` (in that order)."""
    client = S["client"]
    tag = f"d{next(_n)}"
    records = [rec(tag, f"m{i}", rv) for i, rv in enumerate(cited)]
    ast = {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [r["source_mapping_id"] for r in records],
           "children": [pred(f"{tag}_a"), pred(f"{tag}_b", fact="entity.financial_entity_type")]}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": ast, "golden_vectors": VECTORS,
        "valid_from": "2025-01-01", "change_class": change_class, "source_mappings": records})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did, tag


def publish(did, **extra):
    return S["client"].post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                            json={"label": f"pcl-{did[:8]}-{next(_n)}", "changelog": "probe", **extra})


def set_lifecycle(table, key, value, lifecycle):
    from app.db import admin_engine
    with admin_engine.begin() as c:
        c.execute(text(f"UPDATE {table} SET lifecycle = :l WHERE {key} = :v"), {"l": lifecycle, "v": value})


def counts(did=None) -> dict:
    from app.db import admin_engine
    with admin_engine.connect() as c:
        out = {"releases": c.execute(text("SELECT count(*) FROM catalog_content_release")).scalar(),
               "rule_versions": c.execute(text("SELECT count(*) FROM catalog_rule_version")).scalar(),
               "audit": c.execute(text("SELECT count(*) FROM platform_audit_event")).scalar(),
               "outbox": c.execute(text("SELECT count(*) FROM platform_outbox_event")).scalar(),
               "record_seq": c.execute(text("SELECT current_seq FROM platform_record_sequence")).scalar()}
        if did:
            out["draft"] = c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).scalar()
        return out


# ------------------------------------------------------------------------------------------------ sequential
def test_eligible_content_publishes_and_every_validated_row_is_locked(client, world):
    """A release citing a PUBLISHED version and binding a VERIFIED one succeeds. The lock set covers the definition's
    provisions, the cited content, every version of their requirement keys (supersession targets) and their provisions,
    in ID order."""
    from app.db import AdminSession
    from app.engines import catalog
    from app.models import ObligationDefinition, RuleDraft
    published = requirement()
    first, _ = verified_draft(definition(published), [published])
    assert publish(first).status_code == 200  # `published` is now PUBLISHED
    newer = requirement(published["requirement_key"])  # VERIFIED successor of the same requirement
    linked = requirement()
    # The definition links both cited versions: a release whose mappings cite a version outside the definition's
    # binding passes the gate but fails the verifier's RULE_SOURCE_MAPPINGS check (an open policy question).
    did, _ = verified_draft(definition(published, linked), [published, linked])
    with AdminSession() as db:
        locked = catalog.lock_release_content(db, db.get(ObligationDefinition, db.get(RuleDraft, did).obligation_definition_id),
                                              db.get(RuleDraft, did))
        db.rollback()
    expected_rv = sorted(r["requirement_version_id"] for r in (published, newer, linked))
    assert locked["requirement_version_ids"] == expected_rv
    assert locked["provision_ids"] == sorted(p["provision_id"] for r in (published, newer, linked) for p in r["provisions"])
    r = publish(did)
    assert r.status_code == 200, r.text
    bound = {b["requirement_version_id"] for b in r.json()["content_release"]["manifest"]["requirement_versions"]}
    assert bound == {published["requirement_version_id"], linked["requirement_version_id"]}
    assert r.json()["requirement_versions"]["published"] == [linked["requirement_version_id"]]  # VERIFIED -> PUBLISHED
    report = client.get(f"/api/catalog/releases/{r.json()['content_release']['content_release_id']}/verify", headers=H(AUTHOR)).json()
    assert report["integrity"] == "INTACT"


CASES = {  # name -> (which row is withdrawn, expected problem code)
    "cited_requirement": ("cited", "requirement", "RULE_SOURCE_MAPPING_NOT_VERIFIED"),
    "cited_provision": ("cited", "provision", "RULE_SOURCE_MAPPING_NOT_VERIFIED"),
    "linked_requirement": ("linked", "requirement", "REQUIREMENT_NOT_VERIFIED"),
    "linked_provision": ("linked", "provision", "PROVISION_NOT_VERIFIED"),
}


def _withdraw(target_rv, what):
    if what == "requirement":
        set_lifecycle("catalog_requirement_version", "requirement_version_id", target_rv["requirement_version_id"], "WITHDRAWN")
    else:
        set_lifecycle("catalog_provision", "provision_id", target_rv["provisions"][0]["provision_id"], "WITHDRAWN")


@pytest.mark.parametrize("case", sorted(CASES))
def test_content_withdrawn_before_publication_is_refused_without_writes(client, world, case):
    """Withdrawn after the draft was reviewed and before publication: the release gate re-reads the locked rows and
    refuses with the existing contract; nothing is written."""
    where, what, code = CASES[case]
    linked, cited = requirement(), requirement()
    did, _ = verified_draft(definition(linked), [cited])
    _withdraw(cited if where == "cited" else linked, what)
    before, fingerprint = counts(did), db_fingerprint()
    r = publish(did)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RELEASE_CONTENT_NOT_VERIFIED", r.text
    assert code in {p["code"] for p in r.json()["detail"]["problems"]}
    assert counts(did) == before and before["draft"] == "VERIFIED" and db_fingerprint() == fingerprint


def test_a_correction_release_gets_the_same_protection(client, world):
    rv = requirement()
    odef = definition(rv)
    first, _ = verified_draft(odef, [rv])
    assert publish(first).status_code == 200
    cited = requirement()
    corr, _ = verified_draft(odef, [rv, cited], change_class="CORRECTION")
    _withdraw(cited, "requirement")
    before = counts(corr)
    r = publish(corr, correction_reason="Corrected mapping.")
    assert r.status_code == 409 and "RULE_SOURCE_MAPPING_NOT_VERIFIED" in {p["code"] for p in r.json()["detail"]["problems"]}
    assert counts(corr) == before


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL)
def _pg():
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")


def _publish_in_thread(did, out, **extra):
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.routers.rules import PublishIn, publish as publish_route
    from app.security import Ctx
    with SessionLocal() as db:
        set_principal(db, None, RELEASE)
        db.execute(text("SET lock_timeout = '30s'"))
        try:
            res = publish_route(did, PublishIn(label=f"pcl-{did[:8]}-{next(_n)}", changelog="race", **extra),
                                Ctx(db=db, actor=db.get(Actor, RELEASE)))
            out["status"], out["result"] = 200, res
        except HTTPException as e:
            out["status"], out["detail"] = e.status_code, e.detail
            db.rollback()
        except Exception as e:  # a deadlock or lock timeout would land here
            out["status"], out["error"] = "error", repr(e)
            db.rollback()


def _pause_publication_after_its_locks(monkeypatch, odef_id):
    """Hold one publication between its content locks and its rule-scope lock (i.e. with every row it validates
    locked, before it takes the record sequence) until released."""
    from app.engines import applicability as appl
    original, reached, go = appl.lock_rule_scope, threading.Event(), threading.Event()

    def held(db, odef, *a, **k):
        if odef == odef_id:
            reached.set()
            assert go.wait(30)
        return original(db, odef, *a, **k)
    monkeypatch.setattr(appl, "lock_rule_scope", held)
    return reached, go


@pytest.mark.parametrize("case", ["cited_requirement", "linked_provision"])
def test_an_uncommitted_withdrawal_is_waited_for_then_refused(client, world, case):
    """The withdrawal holds the row: publication waits for it, re-reads the committed lifecycle and refuses."""
    _pg()
    from app.db import admin_engine
    where, what, code = CASES[case]
    linked, cited = requirement(), requirement()
    did, _ = verified_draft(definition(linked), [cited])
    target = cited if where == "cited" else linked
    before = counts(did)
    out: dict = {}
    with admin_engine.connect() as c:
        tx = c.begin()
        if what == "requirement":
            c.execute(text("UPDATE catalog_requirement_version SET lifecycle = 'WITHDRAWN' WHERE requirement_version_id = :v"),
                      {"v": target["requirement_version_id"]})
        else:
            c.execute(text("UPDATE catalog_provision SET lifecycle = 'WITHDRAWN' WHERE provision_id = :v"),
                      {"v": target["provisions"][0]["provision_id"]})
        worker = threading.Thread(target=_publish_in_thread, args=(did, out))
        worker.start()
        worker.join(1.5)
        assert worker.is_alive() and not out, "publication must wait for the row being withdrawn"
        tx.commit()
    worker.join(30)
    assert not worker.is_alive() and out["status"] == 409, out
    assert out["detail"]["error"] == "RELEASE_CONTENT_NOT_VERIFIED" and code in {p["code"] for p in out["detail"]["problems"]}
    assert counts(did) == before


def test_a_withdrawal_after_publication_has_locked_waits_for_the_release(client, world, monkeypatch):
    """The publication has locked and validated its content: a withdrawal of a bound version waits until the release
    has committed, so the release binds the version as it was validated and the withdrawal is applied after it."""
    _pg()
    from app.db import admin_engine
    rv = requirement()
    odef = definition(rv)
    did, _ = verified_draft(odef, [rv])
    reached, go = _pause_publication_after_its_locks(monkeypatch, odef)
    out, withdrawal = {}, {}
    pub = threading.Thread(target=_publish_in_thread, args=(did, out))
    pub.start()
    assert reached.wait(30)

    def withdraw():
        try:
            with admin_engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout = '30s'"))
                c.execute(text("UPDATE catalog_requirement_version SET lifecycle = 'WITHDRAWN' WHERE requirement_version_id = :v"),
                          {"v": rv["requirement_version_id"]})
            withdrawal["done"] = True
        except Exception as e:
            withdrawal["error"] = repr(e)
    w = threading.Thread(target=withdraw)
    w.start()
    w.join(1.5)
    assert w.is_alive() and not withdrawal, "the withdrawal must wait for the publication holding the row"
    go.set()
    pub.join(30)
    w.join(30)
    assert out["status"] == 200, out
    assert withdrawal == {"done": True}
    manifest = out["result"]["content_release"]["manifest"]
    assert [b["requirement_version_id"] for b in manifest["requirement_versions"]] == [rv["requirement_version_id"]]
    assert out["result"]["requirement_versions"]["published"] == [rv["requirement_version_id"]]


def test_a_concurrent_supersession_is_serialised_and_a_stale_correction_is_refused(client, world, monkeypatch):
    """The realistic race: publication B releases a newer version of a requirement and supersedes the older one, while
    correction release A of another definition still cites the older one. A waits for B's locks, then sees the version
    SUPERSEDED and is refused; no deadlock, and A writes nothing."""
    _pg()
    old = requirement()
    odef_a = definition(old)
    first, _ = verified_draft(odef_a, [old])
    assert publish(first).status_code == 200  # `old` is PUBLISHED
    new = requirement(old["requirement_key"])
    odef_b = definition(new)
    b_did, _ = verified_draft(odef_b, [new])
    a_did, _ = verified_draft(odef_a, [old], change_class="CORRECTION")
    reached, go = _pause_publication_after_its_locks(monkeypatch, odef_b)
    out_b, out_a = {}, {}
    tb = threading.Thread(target=_publish_in_thread, args=(b_did, out_b))
    tb.start()
    assert reached.wait(30)
    before_a = counts(a_did)
    ta = threading.Thread(target=_publish_in_thread, args=(a_did, out_a), kwargs={"correction_reason": "Stale correction."})
    ta.start()
    ta.join(1.5)
    assert ta.is_alive() and not out_a, "A must wait for the requirement rows B holds"
    go.set()
    tb.join(30)
    ta.join(30)
    assert out_b["status"] == 200, out_b
    assert out_b["result"]["requirement_versions"]["superseded"] == [old["requirement_version_id"]]
    assert out_a["status"] == 409 and out_a["detail"]["error"] == "RELEASE_CONTENT_NOT_VERIFIED", out_a
    assert {"REQUIREMENT_NOT_VERIFIED", "RULE_SOURCE_MAPPING_NOT_VERIFIED"} & {p["code"] for p in out_a["detail"]["problems"]}
    after_a = counts(a_did)
    # A wrote nothing: everything that changed is B's one release (one rule version, one record sequence number).
    assert after_a["draft"] == "VERIFIED"
    assert (after_a["releases"], after_a["rule_versions"], after_a["record_seq"]) == \
        (before_a["releases"] + 1, before_a["rule_versions"] + 1, before_a["record_seq"] + 1)


def test_concurrent_publications_over_overlapping_content_do_not_deadlock(client, world):
    """Four publications cite the same requirement versions in different orders and start together. The shared ID
    order means none can wait on another in a cycle: all four commit."""
    _pg()
    shared = [requirement() for _ in range(3)]
    drafts = []
    for i in range(4):
        order = shared[:]
        random.Random(i).shuffle(order)
        drafts.append(verified_draft(definition(*order), order)[0])
    outs = [{} for _ in drafts]
    start = threading.Barrier(len(drafts))

    def run(did, out):
        start.wait(10)
        _publish_in_thread(did, out)
    threads = [threading.Thread(target=run, args=(did, out)) for did, out in zip(drafts, outs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads)
    assert [o["status"] for o in outs] == [200] * 4, outs
