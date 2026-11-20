"""Subject applicability history: GET /subjects/{id}/applicability (read-only).

Controlling text:
  * ZA-API-001 §14 "GET /v1/subjects/{type}/{id}/applicability: List current/historical evaluations and determinations.
    Cursor + temporal perspective"; §10 (both temporal axes explicit; pagination cursor pins the temporal query
    context); §11 / API-REQ-012 (opaque cursor, stable total order, record watermark pinned) and API-REQ-023
    (allowlisted typed filters).
  * ZA-TIM-001 §16 "GET /subjects/{type}/{id}/history: Returns lineage/revisions with valid and record intervals under
    authorization"; "Pagination tokens for historical data include the temporal query context so page 2 cannot silently
    use a newer watermark"; TIM-REQ-017 (the resolved watermark is echoed) and TIM-REQ-019 (no cutoff beyond the data).
  * ZA-APP-001 §16 WF-APP-01 History: "Timeline of evaluations ... customer determinations ... with valid/record-time
    perspective".
  * QA-REQ-019 (cross-tenant reads), IAM-REQ-009/010 (server-resolved entity scope).

Pinned here: the listing holds exactly the subject's evaluations and determination revisions recorded by the pinned
watermark, in one total order (record sequence, evaluation before determination, ID); pages walk it without gaps or
repeats and never read newer knowledge; a past record sequence shows each revision as then known (a later closure is not
shown); summaries expose no fact value, trace or bundle hash; another organisation's subject is not found and its records
never appear; an entity-scoped reader reaches its own entity only; and no read writes anything.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from conftest import H, add_principal

TAG = secrets.token_hex(3)
TA, TB = f"tenant-sah-a-{TAG}", f"tenant-sah-b-{TAG}"
CCO_A, CCO_B, AUDITOR_A, TSA_A, ENTITY_READER = (f"sah-{r}-{TAG}" for r in ("cco-a", "cco-b", "aud-a", "tsa-a", "ent"))
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"SAH-{TAG.upper()}-REG"
F, G = (f"sah{TAG}.{n}" for n in ("flag", "gate"))
A, C = (f"SAH_{TAG.upper()}_{n}" for n in "AC")
NOW = datetime.now(timezone.utc).replace(microsecond=0)
S: dict = {}

ENVELOPE_KEYS, PAGE_KEYS = {"items", "page"}, {"next_cursor", "has_more", "watermark", "limit"}
WATERMARK_KEYS = {"cell_id", "record_seq", "perspective"}
EVALUATION_KEYS = {"kind", "record_seq", "evaluation_id", "obligation_definition", "rule_version", "effective_at",
                   "record_cutoff_seq", "outcome", "evaluator_build", "trigger", "missing_facts",
                   "non_material_unknowns", "requested_by", "recorded_at"}
DETERMINATION_KEYS = {"kind", "record_seq", "obligation_definition", "revision_id", "lineage_id", "evaluation_id",
                      "outcome", "state", "basis", "needs_review", "review_reason", "valid_from", "valid_to",
                      "record_from_seq", "record_to_seq", "recorded_at", "actor_id", "current_at_watermark"}
NEVER_LISTED = {"input_bundle", "input_bundle_hash", "evaluation_key", "trace", "value"}


def ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


# ------------------------------------------------------------------------------------------------------------ fixture
def _publish(client, key: str, fact: str, requirement: dict, src: dict) -> str:
    """ObligationDefinition `key` whose rule APPLIES iff `fact` is True."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=key, title=f"History {key}", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in requirement["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = f"sah{secrets.token_hex(3)}", requirement["provisions"][0]
    ast = {"node_type": "PREDICATE", "node_id": f"{t}_p", "operator": "EQ", "source_mappings": [f"sm_{t}_p"],
           "args": [{"node_type": "FACT_REF", "node_id": f"{t}_pf", "fact": fact},
                    {"node_type": "CONST", "node_id": f"{t}_pc", "type": "BOOLEAN", "value": True}]}
    maps = [{"source_mapping_id": f"sm_{t}_p", "requirement_version_id": requirement["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": src["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}]

    def vec(vid, cls, value, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {fact: value}, "expected_outcome": expected}
    body = {"obligation_definition_id": odef, "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps, "ast": ast,
            "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"sah-{secrets.token_hex(4)}", "changelog": "subject history probe"})
    assert r.status_code == 200, r.text
    return odef


def _evaluate(client, actor, sid, odef, at: datetime) -> dict:
    r = client.post("/api/applicability/evaluations", headers=H(actor),
                    json={"subject_id": sid, "obligation_definition_id": odef, "effective_at": at.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    """Organisation A: subject `one` (entity e-one) with, in record order, E1+R1 (first APPLIES evaluation and its
    PROPOSED revision, one sequence), E2+R2 (a refresh that closes R1), E3 (UNCERTAIN: no revision), R3 (ADOPTED by
    attestation, closing R2; no evaluation) and E4 (a later evaluation of an earlier valid instant: no revision);
    subject `two` (entity e-two) with one evaluation and revision. Organisation B: subject `other`, evaluated the
    same way, for isolation."""
    from app.db import CELL_ID, AdminSession
    from app.models import FactDefinition, Instrument, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for t in (TA, TB):
            db.add(Tenant(tenant_id=t, legal_name=f"{t} Group", contracting_region="EU"))
        for key in (F, G):
            db.add(FactDefinition(key=key, label=key, data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                                  sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.flush()
        for name, tenant in (("one", TA), ("two", TA), ("other", TB)):
            sid, eid = f"s-sah-{name}-{TAG}", f"e-sah-{name}-{TAG}"
            db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"SAH {name} Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.flush()
            db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"SAH {name} Ltd"))
            S[name], S[f"entity_{name}"] = sid, eid
        for aid, tenant, roles in ((CCO_A, TA, ["CCO"]), (CCO_B, TB, ["CCO"]), (AUDITOR_A, TA, ["EXTERNAL_AUDITOR"]),
                                   (TSA_A, TA, ["TENANT_SECURITY_ADMIN"]), (ENTITY_READER, TA, [])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.add(Instrument(identifier=INSTR, title="History Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    # An entity-scoped reader, granted through the governed role registry (IAM-REQ-010).
    r = client.post("/api/role-assignments", headers=H(TSA_A), json={
        "principal_id": ENTITY_READER, "role_code": "INTERNAL_AUDIT", "scope_type": "LEGAL_ENTITY",
        "scope_id": S["entity_one"], "reason": "subject history entity probe"})
    assert r.status_code == 201, r.text
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "History Act",
        "locator": f"OJ L {TAG}/sah", "instrument_identifier": INSTR, "raw_text": "Entities shall keep a ledger."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"SAH-{TAG}-REQ",
        "title": "Ledger requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep ledger",
             "regulated_object": "ledger", "timing": "continuous", "normalized_meaning": "Keep a ledger.",
             "evidence_expectation": "Ledger"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["odef"] = {k: _publish(client, k, fact, rv.json(), src.json()) for k, fact in ((A, F), (C, G))}
    for name, actor in (("one", CCO_A), ("two", CCO_A), ("other", CCO_B)):
        r = client.post(f"/api/subjects/{S[name]}/facts", headers=H(actor),
                        json={"fact_key": F, "value": True, "valid_from": ago(400).isoformat()})
        assert r.status_code == 200, r.text
    e1 = _evaluate(client, CCO_A, S["one"], S["odef"][A], NOW)
    e2 = _evaluate(client, CCO_A, S["one"], S["odef"][A], NOW)
    e3 = _evaluate(client, CCO_A, S["one"], S["odef"][C], NOW)
    assert (e1["outcome"], e2["outcome"], e3["outcome"]) == ("APPLIES", "APPLIES", "UNCERTAIN")
    lineage = e2["current_determination"]["lineage_id"]
    r = client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO_A),
                    json={"decision": "ADOPT", "authority_basis": "Board resolution (history probe)"})
    assert r.status_code == 200, r.text
    e4 = _evaluate(client, CCO_A, S["one"], S["odef"][A], ago(300))
    S["e"] = [e1, e2, e3, e4]
    S["lineage"] = lineage
    S["revisions"] = _revisions(S["one"])
    assert [d.state for d in S["revisions"]] == ["PROPOSED", "PROPOSED", "ADOPTED"]
    S["two_eval"] = _evaluate(client, CCO_A, S["two"], S["odef"][A], NOW)
    S["other_eval"] = _evaluate(client, CCO_B, S["other"], S["odef"][A], NOW)
    return True


def _revisions(subject_id):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination as D
    with AdminSession() as db:
        rows = db.execute(select(D).where(D.subject_id == subject_id)
                          .order_by(D.record_from_seq, D.revision_id)).scalars().all()
        db.expunge_all()
        return rows


def _oracle(subject_id, known: int, odef: str | None = None) -> list[tuple]:
    """Every evaluation and determination revision of the subject recorded by `known`, read straight from the tables:
    (kind, id, record sequence, record_to_seq as known then), in the documented order."""
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination as D
    from app.models import ApplicabilityEvaluation as E
    with AdminSession() as db:
        evs = db.execute(select(E).where(E.subject_id == subject_id, E.record_seq <= known)).scalars().all()
        dets = db.execute(select(D).where(D.subject_id == subject_id, D.record_from_seq <= known)).scalars().all()
        rows = [(e.record_seq, 0, e.evaluation_id, ("EVALUATION", e.evaluation_id, e.record_seq, None))
                for e in evs if odef in (None, e.obligation_definition_id)]
        rows += [(d.record_from_seq, 1, d.revision_id,
                  ("DETERMINATION", d.revision_id, d.record_from_seq,
                   d.record_to_seq if d.record_to_seq is not None and d.record_to_seq <= known else None))
                 for d in dets if odef in (None, d.obligation_definition_id)]
        return [r[3] for r in sorted(rows)]


def _summary(items) -> list[tuple]:
    return [(i["kind"], i["evaluation_id"] if i["kind"] == "EVALUATION" else i["revision_id"], i["record_seq"],
             None if i["kind"] == "EVALUATION" else i["record_to_seq"]) for i in items]


def _get(client, sid, actor=CCO_A, **params):
    return client.get(f"/api/subjects/{sid}/applicability", headers=H(actor), params=params)


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _walk(client, sid, limit, actor=CCO_A, **params) -> tuple[list, list]:
    """All pages of one listing: (items, the watermark each page echoed)."""
    items, marks, cursor = [], [], None
    for _ in range(200):
        body = _ok(_get(client, sid, actor, limit=limit, **params, **({"cursor": cursor} if cursor else {})))
        assert len(body["items"]) <= limit
        items += body["items"]
        marks.append(body["page"]["watermark"])
        cursor = body["page"]["next_cursor"]
        assert body["page"]["has_more"] is (cursor is not None)
        if cursor is None:
            return items, marks
    raise AssertionError("pagination did not terminate")


def _watermark():
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


# ------------------------------------------------------------------------------------------------------------ order
def test_lists_every_evaluation_and_determination_revision_in_record_order(client, world):
    body = _ok(_get(client, S["one"], limit=200))
    known = body["page"]["watermark"]["record_seq"]
    assert _summary(body["items"]) == _oracle(S["one"], known)
    e1, e2, e3, e4 = (e["evaluation_id"] for e in S["e"])
    r1, r2, r3 = (d.revision_id for d in S["revisions"])
    listed = [x[1] for x in _summary(body["items"])]
    # The world's records, then only evaluations another test records later (none of them closes a revision).
    assert listed[:7] == [e1, r1, e2, r2, e3, r3, e4]
    assert all(i["kind"] == "EVALUATION" for i in body["items"][7:])
    # An evaluation and the revision it produced share the transaction's sequence; the evaluation is listed first.
    by_id = {x[1]: x[2] for x in _summary(body["items"])}
    assert by_id[e1] == by_id[r1] and by_id[e2] == by_id[r2]
    # The current perspective: R1 and R2 show their closures (by R2 and R3), R3 is the lineage's current revision.
    dets = {i["revision_id"]: i for i in body["items"] if i["kind"] == "DETERMINATION"}
    assert (dets[r1]["record_to_seq"], dets[r2]["record_to_seq"], dets[r3]["record_to_seq"]) == (
        by_id[r2], by_id[r3], None)
    assert [dets[r]["current_at_watermark"] for r in (r1, r2, r3)] == [False, False, True]
    assert body["page"]["watermark"]["perspective"] == "CURRENT" and body["page"]["has_more"] is False


def test_items_are_summaries_of_the_stored_records_without_fact_values(client, world):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination, ApplicabilityEvaluation, RuleVersion
    from app.platform import to_dict
    body = _ok(_get(client, S["one"]))
    assert set(body) == ENVELOPE_KEYS and set(body["page"]) == PAGE_KEYS
    assert set(body["page"]["watermark"]) == WATERMARK_KEYS and body["page"]["limit"] == 50
    with AdminSession() as db:
        for item in body["items"]:
            assert not NEVER_LISTED & set(item), item
            if item["kind"] == "EVALUATION":
                assert set(item) == EVALUATION_KEYS
                stored = to_dict(db.get(ApplicabilityEvaluation, item["evaluation_id"]))
                for k in ("effective_at", "record_cutoff_seq", "outcome", "evaluator_build", "trigger",
                          "missing_facts", "non_material_unknowns", "requested_by"):
                    assert item[k] == stored[k], k
                assert (item["record_seq"], item["recorded_at"]) == (stored["record_seq"], stored["created_at"])
                rv = db.get(RuleVersion, stored["rule_version_id"])
                assert item["rule_version"] == {"rule_version_id": rv.rule_version_id, "version": rv.version,
                                                "content_release_id": rv.content_release_id}
                odef = stored["obligation_definition_id"]
            else:
                assert set(item) == DETERMINATION_KEYS
                stored = to_dict(db.get(ApplicabilityDetermination, item["revision_id"]))
                for k in DETERMINATION_KEYS - {"kind", "record_seq", "obligation_definition", "current_at_watermark"}:
                    assert item[k] == stored[k], k
                assert item["record_seq"] == stored["record_from_seq"]
                odef = stored["obligation_definition_id"]
            key = A if odef == S["odef"][A] else C
            assert item["obligation_definition"] == {"obligation_definition_id": odef, "key": key,
                                                     "title": f"History {key}"}
    uncertain = next(i for i in body["items"] if i.get("evaluation_id") == S["e"][2]["evaluation_id"]
                     and i["kind"] == "EVALUATION")
    assert uncertain["outcome"] == "UNCERTAIN" and uncertain["missing_facts"] == [G]


# ------------------------------------------------------------------------------------------------------------ paging
@pytest.mark.parametrize("limit", [1, 2, 3])
def test_pages_walk_one_total_order_without_gaps_or_repeats(client, world, limit):
    full = _ok(_get(client, S["one"], limit=200))
    items, marks = _walk(client, S["one"], limit)
    assert _summary(items) == _summary(full["items"])
    assert len({(i["kind"], i.get("evaluation_id"), i.get("revision_id")) for i in items}) == len(items)
    assert all(m == marks[0] for m in marks)


def test_later_pages_keep_the_first_pages_watermark(client, world):
    first = _ok(_get(client, S["two"], limit=1))
    pinned = first["page"]["watermark"]
    assert first["page"]["has_more"] is True
    late = _evaluate(client, CCO_A, S["two"], S["odef"][A], NOW)  # recorded after page 1
    rest, cursor = [], first["page"]["next_cursor"]
    while cursor:
        body = _ok(_get(client, S["two"], limit=1, cursor=cursor))
        assert body["page"]["watermark"] == pinned
        rest += body["items"]
        cursor = body["page"]["next_cursor"]
    walked = first["items"] + rest
    assert late["evaluation_id"] not in {i.get("evaluation_id") for i in walked}
    assert _summary(walked) == _oracle(S["two"], pinned["record_seq"])
    fresh = _ok(_get(client, S["two"], limit=200))
    assert fresh["page"]["watermark"]["record_seq"] > pinned["record_seq"]
    assert late["evaluation_id"] in {i.get("evaluation_id") for i in fresh["items"]}


# ------------------------------------------------------------------------------------------------------------ history
def test_a_past_record_sequence_lists_what_was_known_then(client, world):
    r1, r2, r3 = S["revisions"]
    e1, e2, e3, e4 = (e["evaluation_id"] for e in S["e"])
    s1, s2, s3 = r1.record_from_seq, r2.record_from_seq, r3.record_from_seq
    cases = {
        s1 - 1: ([], {}),
        s1: ([e1, r1.revision_id], {r1.revision_id: (None, True)}),
        s2 - 1: ([e1, r1.revision_id], {r1.revision_id: (None, True)}),
        s2: ([e1, r1.revision_id, e2, r2.revision_id], {r1.revision_id: (s2, False), r2.revision_id: (None, True)}),
        s3: ([e1, r1.revision_id, e2, r2.revision_id, e3, r3.revision_id],
             {r1.revision_id: (s2, False), r2.revision_id: (s3, False), r3.revision_id: (None, True)}),
    }
    answers = {}
    for k, (ids, dets) in cases.items():
        body = _ok(_get(client, S["one"], as_of_record_seq=k, limit=200))
        assert body["page"]["watermark"]["record_seq"] == k
        assert body["page"]["watermark"]["perspective"] == "AS_OF_RECORD_SEQ"
        assert [x[1] for x in _summary(body["items"])] == ids, k
        assert _summary(body["items"]) == _oracle(S["one"], k)
        for item in body["items"]:
            if item["kind"] == "DETERMINATION":
                assert (item["record_to_seq"], item["current_at_watermark"]) == dets[item["revision_id"]], (k, item)
        answers[k] = body
    # History does not move: a later record leaves every past answer byte-identical.
    _evaluate(client, CCO_A, S["one"], S["odef"][A], ago(100))
    for k, before in answers.items():
        assert _ok(_get(client, S["one"], as_of_record_seq=k, limit=200)) == before, k
    assert e4 not in {i.get("evaluation_id") for i in answers[s3]["items"]}
    # Paged at a past sequence: every page pins that sequence and the pages make up the same list.
    items, marks = _walk(client, S["one"], 1, as_of_record_seq=s2)
    assert _summary(items) == _summary(answers[s2]["items"])
    assert {m["record_seq"] for m in marks} == {s2} and {m["perspective"] for m in marks} == {"AS_OF_RECORD_SEQ"}


def test_a_record_sequence_beyond_the_watermark_is_refused(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    r = _get(client, S["one"], as_of_record_seq=_watermark() + 1000)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE", r.text
    assert db_fingerprint() == before


def test_the_obligation_definition_filter_is_exact(client, world):
    for key in (A, C):
        body = _ok(_get(client, S["one"], obligation_definition_id=S["odef"][key], limit=200))
        known = body["page"]["watermark"]["record_seq"]
        assert body["items"] and _summary(body["items"]) == _oracle(S["one"], known, S["odef"][key])
        assert {i["obligation_definition"]["key"] for i in body["items"]} == {key}
    only_c = _ok(_get(client, S["one"], obligation_definition_id=S["odef"][C]))
    assert [i["evaluation_id"] for i in only_c["items"]] == [S["e"][2]["evaluation_id"]]
    empty = _ok(_get(client, S["one"], obligation_definition_id="no-such-definition"))
    assert empty["items"] == [] and empty["page"]["has_more"] is False


# ------------------------------------------------------------------------------------------------------------ cursor
def test_a_cursor_is_bound_to_its_query_scope_and_signature(client, world):
    from app import pagination
    first = _ok(_get(client, S["one"], limit=1))
    cursor = first["page"]["next_cursor"]
    assert cursor
    for params in ({"limit": 2}, {"limit": 1, "obligation_definition_id": S["odef"][A]},
                   {"limit": 1, "as_of_record_seq": first["page"]["watermark"]["record_seq"]}):
        r = _get(client, S["one"], cursor=cursor, **params)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor", (params, r.text)
    r = _get(client, S["two"], limit=1, cursor=cursor)  # another subject's listing
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"
    body, sig = cursor.split(".", 1)
    tampered = body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1] + "." + sig
    r = _get(client, S["one"], limit=1, cursor=tampered)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"
    audit_cursor = _ok(client.get("/api/audit-events", headers=H(CCO_A), params={"limit": 1}))["page"]["next_cursor"]
    if audit_cursor:  # a cursor of another collection is refused, never reinterpreted
        r = _get(client, S["one"], limit=1, cursor=audit_cursor)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"
    fp = pagination.query_fingerprint({"subject_id": S["one"], "obligation_definition_id": None,
                                       "as_of_record_seq": None, "limit": 1, "sort": "record_seq,kind,id"})
    expired = pagination.encode_cursor(scope=f"subject-applicability:{TA}", query=fp, position=[0, 0, ""],
                                       watermark=first["page"]["watermark"]["record_seq"],
                                       now=time.time() - 2 * pagination.CURSOR_TTL_SECONDS)
    r = _get(client, S["one"], limit=1, cursor=expired)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "cursor_expired"


def test_parameters_are_typed_and_bounded(client, world):
    for params in ({"limit": 0}, {"limit": 201}, {"as_of_record_seq": -1}, {"obligation_definition_id": "x" * 37},
                   {"cursor": ""}):
        assert _get(client, S["one"], **params).status_code == 422, params


# ------------------------------------------------------------------------------------------------------------ scope
def test_another_organisations_subject_is_not_found_and_its_records_never_listed(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    assert _get(client, S["one"], actor=CCO_B).status_code == 404
    assert _get(client, S["other"], actor=CCO_A).status_code == 404
    assert _get(client, S["other"], actor=CCO_A, as_of_record_seq=1).status_code == 404
    theirs = _ok(_get(client, S["other"], actor=CCO_B, limit=200))
    assert {i.get("evaluation_id") for i in theirs["items"] if i["kind"] == "EVALUATION"} == {
        S["other_eval"]["evaluation_id"]}
    ours = _ok(_get(client, S["one"], limit=200))
    foreign = {S["other_eval"]["evaluation_id"], S["other_eval"]["current_determination"]["revision_id"]}
    listed = {v for i in ours["items"] for v in (i.get("evaluation_id"), i.get("revision_id")) if v}
    assert listed and not foreign & listed
    # A's cursor presented by B on B's own subject: another scope, refused.
    cursor = _ok(_get(client, S["one"], limit=1))["page"]["next_cursor"]
    r = _get(client, S["other"], actor=CCO_B, limit=1, cursor=cursor)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_cursor"
    assert db_fingerprint() == before


def test_an_entity_scoped_reader_reaches_its_own_entity_only(client, world):
    mine = _ok(_get(client, S["one"], actor=ENTITY_READER, limit=200))
    assert _summary(mine["items"]) == _summary(_ok(_get(client, S["one"], limit=200))["items"])
    assert _get(client, S["two"], actor=ENTITY_READER).status_code == 404


def test_read_authorization_follows_the_evaluation_and_determination_reads(client, world):
    from test_authorization import db_fingerprint, denials
    assert _get(client, S["one"], actor=AUDITOR_A).status_code == 200  # DOSSIER_READ: the auditor allowlist
    before, n = db_fingerprint(), denials(TSA_A)
    assert _get(client, S["one"], actor=TSA_A).status_code == 403  # a role administrator reads no records
    assert denials(TSA_A) == n + 1
    assert client.get(f"/api/subjects/{S['one']}/applicability").status_code == 401
    assert db_fingerprint() == before


def test_reads_write_nothing(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    _walk(client, S["one"], 2)
    _ok(_get(client, S["one"], as_of_record_seq=S["revisions"][1].record_from_seq))
    _ok(_get(client, S["one"], obligation_definition_id=S["odef"][C]))
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------------------ cost
def test_statements_per_page_do_not_grow_with_the_page(client, world):
    """The related definitions and rule versions are read once per page, not once per item."""
    from sqlalchemy.engine import Engine
    counts = {}
    for limit in (1, 7):
        n = []

        def hook(*_a, n=n):
            n.append(1)
        event.listen(Engine, "before_cursor_execute", hook)
        try:
            # Pinned at E4: exactly the world's seven records, whatever other tests record later.
            body = _ok(_get(client, S["one"], limit=limit, as_of_record_seq=S["e"][3]["record_seq"]))
        finally:
            event.remove(Engine, "before_cursor_execute", hook)
        counts[limit] = (len(n), len(body["items"]))
    assert counts[7][1] == 7 and counts[1][1] == 1
    assert counts[1][0] == counts[7][0], counts
