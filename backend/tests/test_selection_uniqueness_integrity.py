"""Historical selection and uniqueness integrity (ZA-DAT-001 DAT-REQ-010): no helper silently selects one of several
records where exactly one may qualify.

The schema guarantees none of these selections (migrations 0001-0013): no UNIQUE (obligation_definition_id, version) on
catalog_rule_version, no partial unique index or exclusion constraint on current determination or obligation revisions.
Uniqueness rests on serialized mutation (the definition row lock at publication, the decision-scope lock at evaluation
and attestation). The helpers therefore refuse an impossible duplicate (500 AMBIGUOUS_CURRENT_RECORD, engines/selection.py)
instead of picking one by row order, recency or ID. The selection rules that ARE defined are unchanged and pinned here:
highest version among PUBLISHED versions in force; current = record_to_seq IS NULL; as of K = the revision whose record
slice [record_from_seq, record_to_seq) contains K.

Covered for resolve_rule, current_rule_version, effective_rule_version, current_determination(s), _find_obl and the
dossier as-of selection, and the mutation paths that make the same selections (attest, challenge, obligation activation
and closure, expression versions, occurrences, publication's predecessor) and the dossier's closing-decision lookup:
zero candidates, exactly one, impossible multiples (refused, nothing written, nothing chosen), historical as-of
boundaries through closure and reopening, rule supersession with replay, another organisation's rows, and on
PostgreSQL 17 concurrent publication (distinct version numbers) and concurrent first evaluation (one lineage, one
current revision).

Impossible states are written below the application: catalogue duplicates only inside a transaction that is rolled back
(published catalogue rows are immutable, and a committed duplicate would reach every catalogue list); tenant revisions
are committed in this module's own organisations and removed afterwards. Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
tag = TAG.lower()
INSTR = f"SUI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-sui-{tag}", f"tenant-sui-b-{tag}"
CCO, CCO_B = f"sui-cco-{tag}", f"sui-cco-b-{tag}"
EU, KIND = "entity.established_in_eu", "entity.financial_entity_type"
CODE = "AMBIGUOUS_CURRENT_RECORD"
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


def _vectors() -> list[dict]:
    def vec(vid, cls, eu, kind, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {EU: eu, KIND: kind}, "expected_outcome": out}
    return [vec("P", "POSITIVE", True, "CREDIT_INSTITUTION", "APPLIES"),
            vec("N", "NEGATIVE", False, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
            vec("U", "UNKNOWN", True, {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]


def _definition(name: str) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"SUI_{TAG}_{name}", title=f"Selection obligation {name}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _draft(client, odef: str) -> str:
    """A draft of a new version, approved and shadowed: ready to publish."""
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]

    def m(sid):
        return {"source_mapping_id": sid, "requirement_version_id": S["rv"]["requirement_version_id"],
                "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(t), "golden_vectors": _vectors(),
        "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m(f"sm_{t}_root"), m(f"sm_{t}_eu"), m(f"sm_{t}_kind")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    return did


def _publish(client, did: str) -> dict:
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"sui-{secrets.token_hex(4)}", "changelog": "sui"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Selection Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="SUI CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="SUI CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Selection Act",
        "locator": f"OJ L {TAG}/sui", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a log."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"SUI-{TAG}-REQ",
        "title": "Log requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep log",
             "regulated_object": "log", "timing": "continuous", "normalized_meaning": "Keep a log.",
             "evidence_expectation": "Log"}]})
    assert rv.status_code == 201, rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["odef"], S["odef_sup"], S["odef_race"] = _definition("A"), _definition("SUP"), _definition("RACE")
    S["empty"] = _definition("EMPTY")  # never published
    for odef in (S["odef"], S["odef_sup"], S["odef_race"]):
        _publish(client, _draft(client, odef))
    return True


def _subject(name: str, tenant=TA) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{tag}"[:36]
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}"[:36], tenant_id=tenant, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=f"e-{sid}"[:36], subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    _fact(sid, EU, True)
    _fact(sid, KIND, "CREDIT_INSTITUTION")
    return sid


def _fact(sid, key, value, *, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=1 if change else 40)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "Changed"}
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text


def _evaluate(sid, odef=None):
    return S["client"].post("/api/applicability/evaluations", headers=H(CCO),
                            json={"subject_id": sid, "obligation_definition_id": odef or S["odef"]})


def _attest(lineage, **extra):
    return S["client"].post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO),
                            json={"decision": "ADOPT", "authority_basis": "Board delegation SUI", **extra})


def _proposed(name) -> dict:
    sid = _subject(name)
    r = _evaluate(sid)
    assert r.status_code == 200, r.text
    assert r.json()["current_determination"]["state"] == "PROPOSED"
    return {"sid": sid, "ev": r.json(), "lineage": r.json()["current_determination"]["lineage_id"]}


def _adopted(name) -> dict:
    x = _proposed(name)
    r = _attest(x["lineage"])
    assert r.status_code == 200, r.text
    return {**x, "obligation": r.json()["obligation"]["lineage_id"]}


def _flag_does_not_apply(x):
    """A newer DOES_NOT_APPLY result flags the adopted determination; adopting it closes the obligation."""
    _fact(x["sid"], KIND, "PAYMENT_INSTITUTION", change=True)


def _flag_applies(x):
    _fact(x["sid"], KIND, "CREDIT_INSTITUTION", change=True)


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _copy(row, **changes):
    """A new row with every column of ``row`` and ``changes`` applied (a below-the-application write)."""
    return type(row)(**({c.name: getattr(row, c.name) for c in type(row).__table__.columns} | changes))


@contextmanager
def _committed(row):
    """Commit a tenant revision row below the application; remove it afterwards."""
    from app.db import AdminSession
    model, key = type(row), type(row).__table__.primary_key.columns.values()[0].name
    with AdminSession() as db:
        db.add(row)
        db.commit()
    try:
        yield row
    finally:
        with AdminSession() as db:
            db.execute(delete(model).where(getattr(model, key) == getattr(row, key)))
            db.commit()


def _watermark() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _ambiguous(r, record_type: str, selection: str, n: int = 2) -> bool:
    d = r.json().get("detail")
    return (r.status_code == 500 and isinstance(d, dict)
            and (d.get("error"), d.get("record_type"), d.get("selection"), d.get("candidates")) == (CODE, record_type,
                                                                                                    selection, n))


def _dossier(lineage, as_of=None, actor=CCO):
    return S["client"].get(f"/api/obligations/{lineage}", headers=H(actor),
                           params={"as_of_record_seq": as_of} if as_of is not None else None)


# ------------------------------------------------------------------------------------------------ rule versions
def _rule_versions(db, odef):
    from app.models import RuleVersion
    return db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == odef)
                      .order_by(RuleVersion.version)).scalars().all()


def test_rule_selection_with_no_version_one_version_and_a_higher_version_in_force(client, world):
    """None: resolve_rule refuses with the APP §12 RULE_NOT_EFFECTIVE, the catalogue helpers answer None. One: all three
    select it. A higher version in force: all three select the highest (the supersession policy, unchanged), and a
    record cutoff before it was recorded still binds the earlier one (historical selection)."""
    from app.db import AdminSession
    from app.engines import applicability as appl, catalog
    from app.models import ObligationDefinition
    today = date.today()
    with AdminSession() as db:
        empty = db.get(ObligationDefinition, S["empty"])
        with pytest.raises(HTTPException) as e:
            appl.resolve_rule(db, empty, today, _watermark())
        assert e.value.status_code == 409 and e.value.detail["error"] == "RULE_NOT_EFFECTIVE"
        assert catalog.current_rule_version(db, S["empty"]) is None
        assert catalog.effective_rule_version(db, S["empty"], today) is None

        odef = db.get(ObligationDefinition, S["odef"])
        [v1] = _rule_versions(db, S["odef"])
        k = _watermark()
        assert appl.resolve_rule(db, odef, today, k) is v1
        assert catalog.current_rule_version(db, S["odef"]) is v1 and catalog.effective_rule_version(db, S["odef"], today) is v1
        try:
            v2 = _copy(v1, rule_version_id=f"sui-{tag}-v2", version=v1.version + 1, record_seq=k + 1)
            db.add(v2)
            db.flush()
            assert appl.resolve_rule(db, odef, today, k + 1) is v2
            assert catalog.current_rule_version(db, S["odef"]) is v2
            assert catalog.effective_rule_version(db, S["odef"], today) is v2
            assert appl.resolve_rule(db, odef, today, k) is v1  # not yet recorded at cutoff k
        finally:
            db.rollback()


def test_a_shared_version_number_is_refused_by_every_rule_selection_and_reported_by_validation(client, world):
    """Two PUBLISHED rows with the selected version number: resolve_rule, effective_rule_version and
    current_rule_version refuse, naming no winner; a cutoff before the duplicate was recorded is unaffected; a duplicate
    below the selected version never makes the selection ambiguous; the traceability validation reports it instead of
    failing."""
    from app.db import AdminSession
    from app.engines import applicability as appl, catalog
    from app.engines.selection import AmbiguousSelection
    from app.models import ObligationDefinition
    today = date.today()
    with AdminSession() as db:
        odef = db.get(ObligationDefinition, S["odef"])
        [v1] = _rule_versions(db, S["odef"])
        k = _watermark()
        try:
            dup = _copy(v1, rule_version_id=f"sui-{tag}-dup", record_seq=k + 1)
            db.add(dup)
            db.flush()
            expected = sorted([v1.rule_version_id, dup.rule_version_id])
            for call, selection in ((lambda: appl.resolve_rule(db, odef, today, k + 1), "rule_version_bound"),
                                    (lambda: catalog.effective_rule_version(db, S["odef"], today), "rule_version_effective"),
                                    (lambda: catalog.current_rule_version(db, S["odef"]), "rule_version_latest")):
                with pytest.raises(AmbiguousSelection) as e:
                    call()
                assert (e.value.status_code, e.value.detail["error"], e.value.detail["selection"]) == (500, CODE, selection)
                assert e.value.detail["candidates"] == 2 and e.value.candidate_ids == expected
                assert not any(i in str(e.value.detail) for i in expected)  # IDs are logged, not returned
            assert appl.resolve_rule(db, odef, today, k) is v1  # as known before the duplicate was recorded

            report = catalog.validate(db)
            [issue] = [i for i in report["issues"] if i["code"] == "RULE_VERSION_AMBIGUOUS"
                       and i["obligation_definition_id"] == S["odef"]]
            assert issue["severity"] == "ERROR" and issue["rule_version_ids"] == expected and report["ok"] is False

            top = _copy(v1, rule_version_id=f"sui-{tag}-top", version=v1.version + 1, record_seq=k + 2)
            db.add(top)
            db.flush()
            assert appl.resolve_rule(db, odef, today, k + 2) is top
            assert catalog.current_rule_version(db, S["odef"]) is top
            assert catalog.effective_rule_version(db, S["odef"], today) is top
        finally:
            db.rollback()


def test_publication_refuses_an_ambiguous_predecessor_then_supersedes_and_replay_keeps_the_recorded_version(client, world):
    """Publication numbers after, and names as superseded, the latest version: with two rows sharing it, it refuses and
    writes nothing. Without them the same draft publishes; the new version is bound from then on, an earlier cutoff
    still binds the old one, and an evaluation made under the old one still replays to its recorded result."""
    from app.db import AdminSession
    from app.engines import applicability as appl, catalog
    from app.engines.selection import AmbiguousSelection
    from app.models import Actor, ObligationDefinition
    from app.routers.rules import PublishIn, publish
    from app.security import Ctx
    sid = _subject("sup")
    first = _evaluate(sid, S["odef_sup"])
    assert first.status_code == 200, first.text
    first = first.json()
    did = _draft(client, S["odef_sup"])
    with AdminSession() as db:
        [v1] = _rule_versions(db, S["odef_sup"])
        v1_id, v1_version = v1.rule_version_id, v1.version
        before = db_fingerprint()
        try:
            db.add(_copy(v1, rule_version_id=f"sui-{tag}-pdup", record_seq=_watermark() + 1))
            db.flush()
            with pytest.raises(AmbiguousSelection) as e:
                publish(did, PublishIn(label=f"sui-{tag}-x", changelog="x"), Ctx(db=db, actor=db.get(Actor, RELEASE)))
            assert e.value.detail["selection"] == "rule_version_latest"
        finally:
            db.rollback()
    assert db_fingerprint() == before

    out = _publish(client, did)  # the same draft, without the impossible row
    assert out["impact"]["status"] == "COMPLETE" and out["impact"]["failed"] == {}, out["impact"]
    v2_id = out["rule_version"]["rule_version_id"]
    with AdminSession() as db:
        odef = db.get(ObligationDefinition, S["odef_sup"])
        v1_again, v2 = _rule_versions(db, S["odef_sup"])
        assert (v1_again.rule_version_id, v2.rule_version_id, v2.version) == (v1_id, v2_id, v1_version + 1)
        assert appl.resolve_rule(db, odef, date.today(), _watermark()).rule_version_id == v2_id
        assert appl.resolve_rule(db, odef, date.today(), first["record_cutoff_seq"]).rule_version_id == v1_id
        assert catalog.current_rule_version(db, S["odef_sup"]).rule_version_id == v2_id
        assert catalog.effective_rule_version(db, S["odef_sup"]).rule_version_id == v2_id
    view = client.get(f"/api/applicability/evaluations/{first['evaluation_id']}", headers=H(CCO)).json()
    assert view["rule_version_id"] == v1_id and view["replay"]["status"] == "REPRODUCED", view["replay"]


# ------------------------------------------------------------------------------------------------ determinations
def test_current_determination_with_none_one_and_another_organisations_rows(client, world):
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ApplicabilityDetermination
    x = _proposed("det-one")
    [cur] = _rows(ApplicabilityDetermination, lineage_id=x["lineage"], record_to_seq=None)
    with AdminSession() as db:
        assert appl.current_determination(db, TA, "no-such-subject", S["odef"]) is None
        assert appl.current_determinations(db, TA, set()) == {}
        assert appl.current_determinations(db, TA, {("no-such-subject", S["odef"])}) == {}
        assert appl.current_determination(db, TA, x["sid"], S["odef"]).revision_id == cur.revision_id
    # Another organisation's current revision naming the same subject, definition and lineage is not a candidate.
    with _committed(_copy(cur, revision_id=f"sui-{tag}-detb", tenant_id=TB)):
        with AdminSession() as db:
            assert appl.current_determination(db, TA, x["sid"], S["odef"]).revision_id == cur.revision_id
            assert {k: d.revision_id for k, d in appl.current_determinations(
                db, TA, {(x["sid"], S["odef"])}).items()} == {(x["sid"], S["odef"]): cur.revision_id}
        r = client.get(f"/api/applicability/evaluations/{x['ev']['evaluation_id']}", headers=H(CCO))
        assert r.status_code == 200 and r.json()["current_determination"]["revision_id"] == cur.revision_id
        assert f"sui-{tag}-detb" not in r.text
        listed = client.get("/api/applicability/determinations", headers=H(CCO_B))
        assert listed.status_code == 200 and cur.revision_id not in listed.text


@pytest.mark.parametrize("recorded", ["same_seq", "later_seq"])
def test_two_current_determinations_are_refused_and_nothing_is_written(client, world, recorded):
    """Whether tied on record_from_seq (previously an arbitrary row) or newer (previously the newer one, silently), a
    second current revision of the scope is refused by every reader and writer; once it is gone the same attestation
    succeeds."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.engines.selection import AmbiguousSelection
    from app.models import ApplicabilityDetermination
    x = _proposed(f"det-two-{recorded[0]}")
    [cur] = _rows(ApplicabilityDetermination, lineage_id=x["lineage"], record_to_seq=None)
    seq = cur.record_from_seq if recorded == "same_seq" else _watermark()
    dup_id = f"sui-{tag}-dup-{recorded[0]}"
    with _committed(_copy(cur, revision_id=dup_id, record_from_seq=seq)):
        with AdminSession() as db:
            for call in (lambda: appl.current_determination(db, TA, x["sid"], S["odef"]),
                         lambda: appl.current_determinations(db, TA, {(x["sid"], S["odef"])})):
                with pytest.raises(AmbiguousSelection) as e:
                    call()
                assert e.value.candidate_ids == sorted([cur.revision_id, dup_id])
        before = db_fingerprint()
        det = ("ApplicabilityDetermination", "determination_current")
        listed = client.get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": x["sid"]})
        assert _ambiguous(listed, *det), listed.text
        assert dup_id not in listed.text and cur.revision_id not in listed.text
        assert _ambiguous(client.get(f"/api/applicability/evaluations/{x['ev']['evaluation_id']}", headers=H(CCO)), *det)
        assert _ambiguous(_attest(x["lineage"]), *det)
        assert _ambiguous(client.post(f"/api/applicability/determinations/{x['lineage']}/challenge", headers=H(CCO),
                                      json={"challenge_class": "FACT_WRONG", "detail": "Wrong"}), *det)
        assert _ambiguous(_evaluate(x["sid"]), *det)
        assert db_fingerprint() == before
    r = _attest(x["lineage"])
    assert r.status_code == 200 and r.json()["determination"]["state"] == "ADOPTED", r.text


# ------------------------------------------------------------------------------------------------ obligations
def test_obligation_selection_with_none_one_and_another_organisations_rows(client, world):
    from app.db import AdminSession
    from app.models import ObligationInstance
    from app.routers.assurance import _find_obl, _obl_as_of
    missing = _dossier(f"no-such-{tag}")
    assert missing.status_code == 404 and missing.json()["detail"] == "obligation not found"
    x = _adopted("obl-one")
    [cur] = _rows(ObligationInstance, lineage_id=x["obligation"])
    s0 = cur.record_from_seq
    before = _dossier(x["obligation"], s0 - 1)
    assert before.status_code == 404 and before.json()["detail"] == "obligation not found as of requested record sequence"
    assert client.get(f"/api/obligations/{x['obligation']}/expression-versions", headers=H(CCO),
                      params={"as_of_record_seq": s0 - 1}).status_code == 404
    for as_of in (None, s0):
        r = _dossier(x["obligation"], as_of)
        assert r.status_code == 200 and r.json()["obligation"]["revision_id"] == cur.revision_id, r.text
    other = f"sui-{tag}-oblb"
    with _committed(_copy(cur, revision_id=other, tenant_id=TB, entity_id=None, subject_id="-",
                          obligation_definition_id="-")):
        with AdminSession() as db:
            assert _find_obl(db, TA, x["obligation"]).revision_id == cur.revision_id
            assert _obl_as_of(db, TA, x["obligation"], s0).revision_id == cur.revision_id
        for as_of in (None, s0):
            r = _dossier(x["obligation"], as_of)
            assert r.status_code == 200 and r.json()["obligation"]["revision_id"] == cur.revision_id, r.text
            assert other not in r.text
        theirs = _dossier(x["obligation"], actor=CCO_B)
        assert theirs.status_code == 200 and theirs.json()["obligation"]["revision_id"] == other, theirs.text
        assert cur.revision_id not in theirs.text


def test_overlapping_record_slices_are_refused_as_of_and_the_current_read_is_unaffected(client, world):
    """Two revisions whose record slices both contain K: the as-of selection refuses (previously the more recently
    recorded one was shown, silently); the current revision is still unique and answers; before either was recorded
    nothing is found."""
    from app.db import AdminSession
    from app.engines.selection import AmbiguousSelection
    from app.models import ObligationInstance
    from app.routers.assurance import _obl_as_of
    x = _adopted("obl-slices")
    [cur] = _rows(ObligationInstance, lineage_id=x["obligation"])
    s0 = cur.record_from_seq
    with _committed(_copy(cur, revision_id=f"sui-{tag}-slice", record_from_seq=s0, record_to_seq=s0 + 1)):
        with AdminSession() as db, pytest.raises(AmbiguousSelection):
            _obl_as_of(db, TA, x["obligation"], s0)
        obl = ("ObligationInstance", "obligation_as_of")
        assert _ambiguous(_dossier(x["obligation"], s0), *obl)
        assert _ambiguous(client.get(f"/api/obligations/{x['obligation']}/expression-versions", headers=H(CCO),
                                     params={"as_of_record_seq": s0}), *obl)
        assert _dossier(x["obligation"], s0 - 1).status_code == 404
        r = _dossier(x["obligation"])
        assert r.status_code == 200 and r.json()["obligation"]["revision_id"] == cur.revision_id


def test_two_current_obligation_revisions_are_refused_by_readers_and_writers(client, world):
    """A second current revision of an obligation: the dossier, its expression versions, occurrence scheduling and the
    closure an attestation makes all refuse and write nothing (previously the closure would have closed one of them and
    left the other current). The register lists rows and still answers. Once the row is gone the closure succeeds."""
    from app.db import AdminSession
    from app.engines import applicability as appl, governance
    from app.engines.selection import AmbiguousSelection
    from app.models import ObligationInstance
    from app.routers.assurance import _find_obl
    x = _adopted("obl-two")
    _flag_does_not_apply(x)
    [cur] = _rows(ObligationInstance, lineage_id=x["obligation"])
    dup_id = f"sui-{tag}-obl2"
    with _committed(_copy(cur, revision_id=dup_id, record_from_seq=_watermark())):
        with AdminSession() as db:
            for call in (lambda: _find_obl(db, TA, x["obligation"]),
                         lambda: governance._current_obligation(db, TA, x["obligation"]),
                         lambda: appl.current_obligation_for_scope(db, TA, x["sid"], S["odef"])):
                with pytest.raises(AmbiguousSelection) as e:
                    call()
                assert e.value.candidate_ids == sorted([cur.revision_id, dup_id])
        before = db_fingerprint()
        obl = ("ObligationInstance", "obligation_current")
        r = _dossier(x["obligation"])
        assert _ambiguous(r, *obl) and dup_id not in r.text, r.text
        assert _ambiguous(client.post(f"/api/obligations/{x['obligation']}/expression-versions", headers=H(CCO),
                                      json={"ast": {"node_type": "CONTROL", "node_id": "c", "control_key": "X"},
                                            "valid_from": NOW().isoformat(), "rationale": "probe"}), *obl)
        assert _ambiguous(client.post(f"/api/obligations/{x['obligation']}/occurrences", headers=H(CCO),
                                      json={"window_from": NOW().isoformat(),
                                            "due_at": (NOW() + timedelta(days=30)).isoformat()}), *obl)
        assert _ambiguous(_attest(x["lineage"]), *obl)  # the closure
        assert db_fingerprint() == before
        assert client.get("/api/obligations", headers=H(CCO)).status_code == 200
    assert _attest(x["lineage"]).status_code == 200
    assert [o.lifecycle for o in sorted(_rows(ObligationInstance, lineage_id=x["obligation"]),
                                        key=lambda o: o.record_from_seq)] == ["ADOPTED", "CLOSED"]


def test_as_of_selection_follows_closure_and_reopening_at_every_boundary(client, world):
    """ADOPTED -> CLOSED -> ADOPTED through the governed workflow. As of each revision's own record sequence the dossier
    shows that revision; one sequence earlier, the one before it (none before the first); the decision history is
    intact at every point and every revision is unique when selected."""
    from app.models import ObligationInstance
    x = _adopted("obl-history")
    _flag_does_not_apply(x)
    assert _attest(x["lineage"]).status_code == 200
    _flag_applies(x)
    assert _attest(x["lineage"]).status_code == 200
    revs = sorted(_rows(ObligationInstance, lineage_id=x["obligation"]), key=lambda o: o.record_from_seq)
    assert [o.lifecycle for o in revs] == ["ADOPTED", "CLOSED", "ADOPTED"]
    assert [o.record_to_seq for o in revs] == [revs[1].record_from_seq, revs[2].record_from_seq, None]
    for i, rev in enumerate(revs):
        at = _dossier(x["obligation"], rev.record_from_seq)
        assert at.status_code == 200, at.text
        assert (at.json()["obligation"]["revision_id"], at.json()["obligation"]["lifecycle"]) == (rev.revision_id,
                                                                                                 rev.lifecycle)
        assert at.json()["obligation"]["record_to_seq"] is None  # its closure was not yet known then
        assert at.json()["decision_history"]["integrity"]["status"] == "INTACT", at.json()["decision_history"]
        earlier = _dossier(x["obligation"], rev.record_from_seq - 1)
        if i == 0:
            assert earlier.status_code == 404
        else:
            assert earlier.json()["obligation"]["revision_id"] == revs[i - 1].revision_id
    assert _dossier(x["obligation"]).json()["obligation"]["revision_id"] == revs[2].revision_id


def test_an_ambiguous_closing_decision_is_reported_not_chosen(client, world):
    """Two adopted determination revisions recorded at a closure's sequence: the decision history reports
    CLOSURE_DECISION_AMBIGUOUS (MISMATCH) with no closing decision, instead of naming the lowest ID; current and
    historical reads still answer."""
    from app.models import ApplicabilityDetermination, ObligationInstance
    x = _adopted("obl-closing")
    _flag_does_not_apply(x)
    assert _attest(x["lineage"]).status_code == 200
    closed = max(_rows(ObligationInstance, lineage_id=x["obligation"]), key=lambda o: o.record_from_seq)
    [closing] = [d for d in _rows(ApplicabilityDetermination, lineage_id=x["lineage"])
                 if d.record_from_seq == closed.record_from_seq]
    intact = _dossier(x["obligation"]).json()["decision_history"]
    assert intact["integrity"]["status"] == "INTACT" and intact["revisions"][1]["closure"] is not None
    other = f"sui-{tag}-closing"
    with _committed(_copy(closing, revision_id=other, record_to_seq=closed.record_from_seq)):
        for as_of in (None, closed.record_from_seq):
            r = _dossier(x["obligation"], as_of)
            assert r.status_code == 200, r.text
            hist = r.json()["decision_history"]
            assert hist["revisions"][1]["closure"] is None and hist["integrity"]["status"] == "MISMATCH"
            [p] = [p for p in hist["revisions"][1]["problems"] if p["code"] == "CLOSURE_DECISION_AMBIGUOUS"]
            assert (p["record_type"], p["candidate_ids"]) == ("ApplicabilityDetermination",
                                                              sorted([closing.revision_id, other]))
    assert _dossier(x["obligation"]).json()["decision_history"] == intact


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and row/advisory locks; reason required by the CI gate
    return True


def _hold(monkeypatch, module, name: str, thread: str):
    """Hold thread ``thread`` inside ``module.name`` until released; other threads pass through."""
    held, release = threading.Event(), threading.Event()
    real = getattr(module, name)

    def holding(*a, **k):
        r = real(*a, **k)
        if threading.current_thread().name == thread:
            held.set()
            assert release.wait(60)
        return r
    monkeypatch.setattr(module, name, holding)
    return held, release


def _run(out: dict, name: str, fn):
    try:
        out[name] = ("ok", fn())
    except HTTPException as e:
        out[name] = (e.status_code, e.detail)
    except Exception as e:  # noqa: BLE001 - reported by the assertions
        out[name] = ("error", repr(e))


def test_concurrent_publications_of_one_definition_number_versions_uniquely(client, world, pg, monkeypatch):
    """Two releases of one definition at once: the second waits on the definition row lock, then numbers after the
    first; every rule selection afterwards has exactly one candidate."""
    from app.db import AdminSession, SessionLocal, set_principal
    from app.engines import catalog
    from app.models import Actor
    from app.routers.rules import PublishIn, publish
    from app.security import Ctx
    d1, d2 = _draft(client, S["odef_race"]), _draft(client, S["odef_race"])
    held, release = _hold(monkeypatch, catalog, "lock_release_content", "first")
    out: dict = {}

    def do(did):
        def go():
            with SessionLocal() as db:
                set_principal(db, None, RELEASE)
                return publish(did, PublishIn(label=f"sui-race-{secrets.token_hex(3)}", changelog="race"),
                               Ctx(db=db, actor=db.get(Actor, RELEASE)))["rule_version"]["version"]
        return go
    a = threading.Thread(target=_run, args=(out, "first", do(d1)), name="first")
    a.start()
    assert held.wait(60), out
    b = threading.Thread(target=_run, args=(out, "second", do(d2)), name="second")
    b.start()
    b.join(2.0)
    waited = b.is_alive()
    release.set()
    a.join(120)
    b.join(120)
    # Both drafts were shadowed while the same version was released: the second waits on the definition row lock, then
    # finds the first released, so its shadow impact no longer compares against the version it would supersede
    # (ZA-APP-001 §10; tests/test_shadow_report_binding.py). Re-shadowed, it numbers after the first.
    assert waited and out["first"][0] == "ok" and out["second"][0] == 409, out
    assert "Shadow impact required" in out["second"][1], out
    assert client.post(f"/api/rules/drafts/{d2}/shadow", headers=H(RELEASE)).status_code == 200
    assert do(d2)() == out["first"][1] + 1
    with AdminSession() as db:
        versions = [v.version for v in _rule_versions(db, S["odef_race"])]
        assert versions == sorted(set(versions)) == [1, 2, 3]
        assert catalog.current_rule_version(db, S["odef_race"]).version == 3
        assert catalog.effective_rule_version(db, S["odef_race"]).version == 3


def _engine_evaluate(sid: str, at: datetime):
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.models import ObligationDefinition, Subject
    with SessionLocal() as db:
        set_principal(db, TA, CCO)
        ev = appl.evaluate(db, tenant_id=TA, subject=db.get(Subject, sid), odef=db.get(ObligationDefinition, S["odef"]),
                           effective_at=at, record_cutoff_seq=None, trigger="EXPLICIT", actor_id=CCO)
        db.commit()
        return ev.evaluation_id


def _race_first_evaluations(monkeypatch, sid: str) -> tuple[dict, bool]:
    from app.engines import applicability as appl
    held, release = _hold(monkeypatch, appl, "lock_decision", "first")
    out: dict = {}
    t = NOW()
    a = threading.Thread(target=_run, args=(out, "first", lambda: _engine_evaluate(sid, t)), name="first")
    a.start()
    assert held.wait(60), out
    b = threading.Thread(target=_run, args=(out, "second", lambda: _engine_evaluate(sid, t + timedelta(seconds=1))),
                         name="second")
    b.start()
    b.join(2.0)
    waited = b.is_alive()
    release.set()
    a.join(120)
    b.join(120)
    return out, waited


def test_concurrent_first_evaluations_leave_one_current_determination(client, world, pg, monkeypatch):
    """A subject's first two evaluations at once: the second waits on the decision-scope lock, then refreshes the
    proposal the first created, so the scope has one lineage and one current revision, which the helpers return."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ApplicabilityDetermination
    sid = _subject("race-eval")
    out, waited = _race_first_evaluations(monkeypatch, sid)
    assert waited and out["first"][0] == "ok" and out["second"][0] == "ok", out
    rows = _rows(ApplicabilityDetermination, subject_id=sid)
    [cur] = [d for d in rows if d.record_to_seq is None]
    assert len({d.lineage_id for d in rows}) == 1
    with AdminSession() as db:
        assert appl.current_determination(db, TA, sid, S["odef"]).revision_id == cur.revision_id
