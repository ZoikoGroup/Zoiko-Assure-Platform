"""Fact-change impact preview: GET /subjects/{id}/facts/impact (read-only).

Controlling text:
  * ZA-APP-001 §16 WF-APP-02 "Impact preview": "Which obligation definitions/evaluations depend on a proposed fact change;
    no predicted legal result shown unless shadow evaluation is explicitly run".
  * ZA-TIM-001 §17 impact index, FactAssertion valid-range change: "Applicability evaluations/rules referencing
    FactDefinition for overlapping subject + valid period; assurance snapshots that depend on resulting obligations".
  * ZA-TIM-001 §16 (no ambiguous query defaults: a valid time is required), TIM-REQ-002 (half-open intervals),
    TIM-REQ-007 / APP-REQ-012 (a correction creates new records and preserves prior history), TIM-REQ-017 (the record
    watermark is echoed).

Pinned here: the preview lists exactly those dependents and the current revisions a write over the interval closes; it
takes no value and predicts no outcome; it writes nothing; and a committed correction afterwards touches exactly what
it listed while every earlier evaluation stays as recorded and still replays. Whether a correction changes an outcome is
shown only by the committed re-evaluation (the existing write path), never by the preview.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3)
TA, TB = f"tenant-fci-a-{TAG}", f"tenant-fci-b-{TAG}"
CCO_A, CCO_B, AUDITOR_A, TSA_A = (f"fci-{r}-{TAG}" for r in ("cco-a", "cco-b", "aud-a", "tsa-a"))
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"FCI-{TAG.upper()}-REG"
F, G, P, X, Y = (f"fci{TAG}.{n}" for n in ("flag", "other", "secret", "unused", "asset_only"))
A, B, C = (f"FCI_{TAG.upper()}_{n}" for n in "ABC")
NOW = datetime.now(timezone.utc).replace(microsecond=0)
REDACTED = "••• (redacted)"
S: dict = {}


def ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def _iso(d: datetime) -> str:
    return d.isoformat()


# ------------------------------------------------------------------------------------------------------------ fixture
def _pred(t: str, nid: str, fact: str) -> dict:
    return {"node_type": "PREDICATE", "node_id": f"{t}_{nid}", "operator": "EQ", "source_mappings": [f"sm_{t}_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{t}_{nid}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{t}_{nid}c", "type": "BOOLEAN", "value": True}]}


def _publish(client, key: str, facts: list[str], requirement: dict, src: dict) -> str:
    """ObligationDefinition `key` whose rule APPLIES iff every fact in `facts` is True (ALL of EQ(f, True))."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=key, title=f"Impact {key}", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in requirement["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = f"fci{secrets.token_hex(3)}", requirement["provisions"][0]
    nids = [f"p{i}" for i in range(len(facts))]
    # ALL needs at least two children: a one-fact rule is its predicate alone.
    ast = (_pred(t, nids[0], facts[0]) if len(facts) == 1 else
           {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"],
            "children": [_pred(t, n, f) for n, f in zip(nids, facts)]})
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": requirement["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": src["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ((*nids,) if len(facts) == 1 else ("root", *nids))]

    def vec(vid, cls, values, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": dict(zip(facts, values)), "expected_outcome": expected}
    rest = [True] * (len(facts) - 1)
    body = {"obligation_definition_id": odef, "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps, "ast": ast,
            "golden_vectors": [vec("P", "POSITIVE", [True, *rest], "APPLIES"),
                               vec("N", "NEGATIVE", [False, *rest], "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", [{"unknown": "MISSING_REQUIRED_FACT"}, *rest], "UNCERTAIN")]}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"fci-{secrets.token_hex(4)}", "changelog": "fact impact probe"})
    assert r.status_code == 200, r.text
    return odef


def _write(client, actor, sid, key, value, valid_from, valid_to=None, **extra):
    body = {"fact_key": key, "value": value, "valid_from": _iso(valid_from), **extra}
    if valid_to is not None:
        body["valid_to"] = _iso(valid_to)
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(actor), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _evaluate(client, actor, sid, odef, at: datetime) -> dict:
    r = client.post("/api/applicability/evaluations", headers=H(actor),
                    json={"subject_id": sid, "obligation_definition_id": odef, "effective_at": _iso(at)})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    """Organisation A: subjects `one` (read-only previews), `two` (committed corrections) and `adopt` (an adopted
    obligation); organisation B: `other`, with the same facts and evaluations, for isolation. Rules: A needs F,
    B needs F and the RESTRICTED_PERSONAL P, C needs G only; X is needed by no rule; Y attaches to another subject type.
    `one` and `two` are evaluated for A, B and C at 300 days ago (T_OLD) and now (T_NOW)."""
    from app.db import CELL_ID, AdminSession
    from app.models import FactDefinition, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for t in (TA, TB):
            db.add(Tenant(tenant_id=t, legal_name=f"{t} Group", contracting_region="EU"))
        for key, sens, stype in ((F, "CUSTOMER_INTERNAL", "LEGAL_ENTITY"), (G, "CUSTOMER_INTERNAL", "LEGAL_ENTITY"),
                                 (P, "RESTRICTED_PERSONAL", "LEGAL_ENTITY"), (X, "CUSTOMER_INTERNAL", "LEGAL_ENTITY"),
                                 (Y, "CUSTOMER_INTERNAL", "AI_ASSET")):
            db.add(FactDefinition(key=key, label=key, data_type="BOOLEAN", subject_type=stype, sensitivity=sens,
                                  manual_attestation_policy="ALLOWED"))
        db.flush()
        for name, tenant in (("one", TA), ("two", TA), ("adopt", TA), ("other", TB)):
            sid, eid = f"s-fci-{name}-{TAG}", f"e-fci-{name}-{TAG}"
            db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"FCI {name} Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.flush()
            db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"FCI {name} Ltd"))
            S[name] = sid
        for aid, tenant, role in ((CCO_A, TA, "CCO"), (CCO_B, TB, "CCO"), (AUDITOR_A, TA, "EXTERNAL_AUDITOR"),
                                  (TSA_A, TA, "TENANT_SECURITY_ADMIN")):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=[role])
        from app.models import Instrument
        db.add(Instrument(identifier=INSTR, title="Impact Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Impact Act",
        "locator": f"OJ L {TAG}/fci", "instrument_identifier": INSTR, "raw_text": "Entities shall keep a register."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"FCI-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["requirement"], S["src"] = rv.json(), src.json()
    S["odef"] = {k: _publish(client, k, facts, rv.json(), src.json()) for k, facts in ((A, [F]), (B, [F, P]), (C, [G]))}
    for name, actor in (("one", CCO_A), ("two", CCO_A), ("adopt", CCO_A), ("other", CCO_B)):
        sid = S[name]
        if name == "two":  # two F slices, so a correction of the later one leaves the earlier one current
            _write(client, actor, sid, F, True, ago(400), ago(200))
            _write(client, actor, sid, F, True, ago(200))
        else:
            _write(client, actor, sid, F, True, ago(400))
        _write(client, actor, sid, G, True, ago(400))
        _write(client, actor, sid, P, True, ago(400))
    S["ev"] = {}
    for name in ("one", "two"):
        for key in (A, B, C):
            for when, at in (("old", ago(300)), ("now", NOW)):
                S["ev"][(name, key, when)] = _evaluate(client, CCO_A, S[name], S["odef"][key], at)["evaluation_id"]
    S["ev"][("adopt", A, "now")] = adopted = _evaluate(client, CCO_A, S["adopt"], S["odef"][A], NOW)
    S["ev"][("other", A, "now")] = _evaluate(client, CCO_B, S["other"], S["odef"][A], NOW)["evaluation_id"]
    lineage = adopted["current_determination"]["lineage_id"]
    r = client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO_A),
                    json={"decision": "ADOPT", "authority_basis": "Board resolution (impact probe)"})
    assert r.status_code == 200, r.text
    S["adopted"] = r.json()
    S["ev"][("adopt", A, "now")] = adopted["evaluation_id"]
    return True


def _preview(client, sid, key, valid_from, valid_to=None, actor=CCO_A):
    params = {"fact_key": key, "valid_from": _iso(valid_from)}
    if valid_to is not None:
        params["valid_to"] = _iso(valid_to)
    return client.get(f"/api/subjects/{sid}/facts/impact", headers=H(actor), params=params)


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _scopes(body) -> dict:
    return {d["obligation_definition"]["key"]: d for d in body["dependents"]}


def _ev_ids(body) -> set[str]:
    return {e["evaluation_id"] for d in body["dependents"] for e in d["evaluations"]}


def _snapshot_of_state():
    """Everything a read must not change: the watermark and the row counts and contents it could touch."""
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination, ApplicabilityEvaluation, FactAssertion, ObligationInstance
    from app.platform import current_watermark
    with AdminSession() as db:
        out = {"watermark": current_watermark(db)["record_seq"]}
        for m in (FactAssertion, ApplicabilityEvaluation, ApplicabilityDetermination, ObligationInstance):
            out[m.__tablename__] = db.execute(select(func.count()).select_from(m)).scalar_one()
        for t in ("platform_audit_event", "platform_outbox_event"):
            from sqlalchemy import text
            out[t] = db.execute(text(f"select count(*) from {t}")).scalar_one()
        return out


def _stored(evaluation_ids) -> dict:
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return {e.evaluation_id: json.loads(json.dumps(
            {"input_bundle": e.input_bundle, "input_bundle_hash": e.input_bundle_hash, "evaluation_key": e.evaluation_key,
             "outcome": e.outcome, "trace": e.trace, "record_cutoff_seq": e.record_cutoff_seq}, default=str))
            for e in db.execute(select(ApplicabilityEvaluation).where(
                ApplicabilityEvaluation.evaluation_id.in_(list(evaluation_ids)))).scalars()}


def _replay_status(client, evaluation_id) -> str:
    return _ok(client.get(f"/api/applicability/evaluations/{evaluation_id}", headers=H(CCO_A)))["replay"]["status"]


# The response contract, stated in full: every object's exact keys. Anything not listed (a proposed, predicted or shadow
# outcome under any name included) fails here, so no prediction can be added without changing this contract.
TOP_KEYS = {"perspective", "proposed_change", "overlapping_revisions", "dependents", "counts", "note"}
PERSPECTIVE_KEYS = {"perspective", "known_seq", "cell_id"}
PROPOSED_KEYS = {"subject_id", "fact_key", "fact_definition_id", "valid_from", "valid_to"}
COUNT_KEYS = {"overlapping_revisions", "scopes", "evaluations", "determinations", "obligations", "assurance_snapshots"}
DEPENDENT_KEYS = {"subject", "obligation_definition", "rule_versions", "evaluations", "determination", "obligation",
                  "assurance_snapshots"}
SUBJECT_KEYS = {"subject_id", "display_name", "subject_type"}
DEFINITION_KEYS = {"obligation_definition_id", "key", "title"}
RULE_VERSION_KEYS = {"rule_version_id", "version", "valid_from", "valid_to"}
EVALUATION_KEYS = {"evaluation_id", "effective_at", "record_cutoff_seq", "record_seq", "rule_version_id",
                   "evaluator_build", "trigger", "recorded_outcome", "fact_input"}
FACT_INPUT_KEYS = ({"fact_assertion_revision_id", "provenance_class"}, {"unknown_reason"})
DETERMINATION_KEYS = {"revision_id", "lineage_id", "state", "recorded_outcome", "evaluation_id", "valid_from",
                      "valid_to", "needs_review", "rests_on_listed_evaluation", "valid_period_overlaps_change"}
OBLIGATION_KEYS = {"revision_id", "lineage_id", "lifecycle", "determination_revision_id", "valid_from", "valid_to",
                   "valid_period_overlaps_change"}
SNAPSHOT_KEYS = {"snapshot_id", "obligation_lineage_id", "period_from", "period_to", "record_cutoff_seq", "state"}
OUTCOMES = {"APPLIES", "DOES_NOT_APPLY", "UNCERTAIN"}


def _assert_contract(body: dict) -> None:
    """Exactly the documented dependency and recorded-outcome information: the keys of every object, the counts
    matching the lists, and each outcome a recorded APPLIES/DOES_NOT_APPLY/UNCERTAIN (never another value)."""
    from app.models import FactAssertion
    revision_keys = {c.key for c in FactAssertion.__table__.columns} | {"value", "fact_key", "fact_label"}
    assert set(body) == TOP_KEYS, set(body) ^ TOP_KEYS
    assert set(body["perspective"]) == PERSPECTIVE_KEYS and body["perspective"]["perspective"] == "CURRENT"
    assert isinstance(body["perspective"]["known_seq"], int)
    assert set(body["proposed_change"]) == PROPOSED_KEYS and isinstance(body["note"], str)
    assert all(set(r) == revision_keys for r in body["overlapping_revisions"]), body["overlapping_revisions"]
    for d in body["dependents"]:
        assert set(d) == DEPENDENT_KEYS, set(d) ^ DEPENDENT_KEYS
        assert set(d["subject"]) == SUBJECT_KEYS and set(d["obligation_definition"]) == DEFINITION_KEYS
        assert all(set(v) == RULE_VERSION_KEYS for v in d["rule_versions"])
        for e in d["evaluations"]:
            assert set(e) == EVALUATION_KEYS and set(e["fact_input"]) in FACT_INPUT_KEYS, e
            assert e["recorded_outcome"] in OUTCOMES
        if d["determination"] is not None:
            assert set(d["determination"]) == DETERMINATION_KEYS
            assert d["determination"]["recorded_outcome"] in OUTCOMES
        if d["obligation"] is not None:
            assert set(d["obligation"]) == OBLIGATION_KEYS
        assert all(set(x) == SNAPSHOT_KEYS for x in d["assurance_snapshots"])
    deps = body["dependents"]
    assert set(body["counts"]) == COUNT_KEYS and body["counts"] == {
        "overlapping_revisions": len(body["overlapping_revisions"]), "scopes": len(deps),
        "evaluations": sum(len(d["evaluations"]) for d in deps),
        "determinations": sum(d["determination"] is not None for d in deps),
        "obligations": sum(d["obligation"] is not None for d in deps),
        "assurance_snapshots": sum(len(d["assurance_snapshots"]) for d in deps)}


def _assert_recorded(body: dict) -> None:
    """Each listed evaluation's and determination's outcome is the one recorded (the stored row), not a computed one."""
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination, ApplicabilityEvaluation
    with AdminSession() as db:
        for d in body["dependents"]:
            for e in d["evaluations"]:
                row = db.get(ApplicabilityEvaluation, e["evaluation_id"])
                assert (e["recorded_outcome"], e["record_seq"]) == (row.outcome, row.record_seq)
            if d["determination"] is not None:
                row = db.get(ApplicabilityDetermination, d["determination"]["revision_id"])
                assert (d["determination"]["recorded_outcome"], d["determination"]["state"]) == (row.outcome, row.state)


# ------------------------------------------------------------------------------------------- dependents and intervals
def test_a_fact_needed_by_several_obligation_definitions_lists_each_dependent_scope(client, world):
    """F is needed by A and B (not C): both scopes are listed, each with the published rule version that needs F; G lists
    only C; X, needed by no published rule, has no dependent at all."""
    body = _ok(_preview(client, S["one"], F, ago(1000)))
    scopes = _scopes(body)
    assert sorted(scopes) == sorted([A, B])
    for key in (A, B):
        d = scopes[key]
        assert d["subject"]["subject_id"] == S["one"]
        assert d["obligation_definition"]["obligation_definition_id"] == S["odef"][key]
        assert len(d["rule_versions"]) == 1 and d["rule_versions"][0]["rule_version_id"]
        assert {e["evaluation_id"] for e in d["evaluations"]} == {S["ev"][("one", key, w)] for w in ("old", "now")}
    assert sorted(_scopes(_ok(_preview(client, S["one"], G, ago(1000))))) == [C]
    none = _ok(_preview(client, S["one"], X, ago(1000)))
    assert none["dependents"] == [] and none["counts"]["scopes"] == 0 and none["counts"]["evaluations"] == 0


def test_only_evaluations_whose_effective_time_lies_in_the_changed_interval_are_listed(client, world):
    """[valid_from, valid_to) is half-open and compared with each recorded evaluation's effective_at: a retroactive
    interval lists the T_OLD evaluations only, an open interval from 100 days ago the T_NOW ones only, an interval
    before every evaluation none (the dependent definitions are still listed), and an interval ending exactly at T_OLD
    excludes it."""
    def listed(valid_from, valid_to=None):
        return _ev_ids(_ok(_preview(client, S["one"], F, valid_from, valid_to)))
    old = {S["ev"][("one", k, "old")] for k in (A, B)}
    now = {S["ev"][("one", k, "now")] for k in (A, B)}
    assert listed(ago(350), ago(200)) == old
    assert listed(ago(100)) == now
    assert listed(ago(1000), ago(900)) == set()
    assert sorted(_scopes(_ok(_preview(client, S["one"], F, ago(1000), ago(900))))) == sorted([A, B])
    assert listed(ago(350), ago(300)) == set()  # T_OLD == valid_to: outside a half-open interval
    assert listed(ago(300), ago(299)) == old    # T_OLD == valid_from: inside
    for d in _ok(_preview(client, S["one"], F, ago(1000)))["dependents"]:
        for e in d["evaluations"]:  # each with the exact revision its frozen bundle consumed, and its recorded outcome
            assert e["fact_input"]["fact_assertion_revision_id"] and e["recorded_outcome"] == "APPLIES", e


def test_the_overlapping_current_revisions_are_named_as_a_write_would_close_them(client, world):
    """`two` holds F in two slices, [400d, 200d) and [200d, open). A change from 100 days ago overlaps only the later
    slice; one from 300 days ago overlaps both; one before 400 days ago overlaps neither."""
    def revs(valid_from, valid_to=None):
        body = _ok(_preview(client, S["two"], F, valid_from, valid_to))
        assert body["counts"]["overlapping_revisions"] == len(body["overlapping_revisions"])
        return [(r["revision_id"], r["valid_from"]) for r in body["overlapping_revisions"]]
    later, both = revs(ago(100)), revs(ago(300))
    assert len(later) == 1 and len(both) == 2 and later[0] in both
    assert both == sorted(both, key=lambda x: x[1])  # ordered by valid time
    assert revs(ago(500), ago(450)) == []


# --------------------------------------------------------------------------------------- read-only and reproducible
def test_the_preview_writes_nothing_and_repeated_calls_give_the_same_answer(client, world):
    """No record, audit row, outbox event or record sequence is written; the stored evaluations and their replay are
    unchanged; the same preview at the same watermark is identical, field for field, and echoes that watermark."""
    ids = {S["ev"][("one", k, w)] for k in (A, B, C) for w in ("old", "now")}
    before, stored = _snapshot_of_state(), _stored(ids)
    first = _ok(_preview(client, S["one"], F, ago(350)))
    second = _ok(_preview(client, S["one"], F, ago(350)))
    assert first == second
    assert first["perspective"]["known_seq"] == before["watermark"] and first["perspective"]["perspective"] == "CURRENT"
    assert _snapshot_of_state() == before
    assert _stored(ids) == stored
    for i in ids:
        assert _replay_status(client, i) == "REPRODUCED"
    # Only the documented dependency and recorded-outcome information, with the dependencies and watermark present.
    _assert_contract(first)
    _assert_recorded(first)
    assert sorted(_scopes(first)) == sorted([A, B]) and _ev_ids(first)  # the dependencies are there


def test_a_sensitive_fact_preview_carries_no_value(client, world):
    """P is RESTRICTED_PERSONAL: its overlapping revision shows the redaction marker and no typed value, and the
    evaluations are named by assertion revision only (no value, no value hash)."""
    r = _preview(client, S["one"], P, ago(1000))
    body = _ok(r)
    (rev,) = body["overlapping_revisions"]
    assert rev["value"] == REDACTED and rev["value_boolean"] is None and rev["revision_id"]
    assert "typed_value_hash" not in r.text and sorted(_scopes(body)) == [B]
    for e in _scopes(body)[B]["evaluations"]:
        assert set(e["fact_input"]) == {"fact_assertion_revision_id", "provenance_class"}


# ------------------------------------------------------------------------------------- decisions downstream
def test_the_adopted_determination_and_obligation_are_listed_with_their_relation_to_the_change(client, world):
    """`adopt`: A's determination was adopted and an obligation activated. A change overlapping its evaluation lists the
    determination as resting on it and both as overlapping; a change entirely before them lists them without either
    relation. Nothing about either is predicted."""
    from app.db import AdminSession
    from app.engines.facts import overlaps
    from app.models import ApplicabilityDetermination, ObligationInstance
    det_id, obl = S["adopted"]["determination"]["revision_id"], S["adopted"]["obligation"]
    with AdminSession() as db:
        det, oi = db.get(ApplicabilityDetermination, det_id), db.get(ObligationInstance, obl["revision_id"])
        periods = {"det": (det.valid_from, det.valid_to), "obl": (oi.valid_from, oi.valid_to)}
    near = _scopes(_ok(_preview(client, S["adopt"], F, ago(10))))[A]
    assert near["evaluations"][0]["evaluation_id"] == S["ev"][("adopt", A, "now")]
    d, o = near["determination"], near["obligation"]
    assert d["revision_id"] == det_id and d["state"] == "ADOPTED" and d["recorded_outcome"] == "APPLIES"
    assert d["rests_on_listed_evaluation"] is True
    assert o["revision_id"] == obl["revision_id"] and o["determination_revision_id"] == det_id
    far = _scopes(_ok(_preview(client, S["adopt"], F, ago(1000), ago(900))))[A]
    assert far["evaluations"] == [] and far["determination"]["revision_id"] == det_id
    assert far["determination"]["rests_on_listed_evaluation"] is False
    for got, interval in ((near, (ago(10), None)), (far, (ago(1000), ago(900)))):  # computed from the stored periods
        assert got["determination"]["valid_period_overlaps_change"] is overlaps(*periods["det"], *interval)
        assert got["obligation"]["valid_period_overlaps_change"] is overlaps(*periods["obl"], *interval)
    assert overlaps(*periods["det"], ago(10), None) and not overlaps(*periods["obl"], ago(1000), ago(900))


def test_assurance_snapshots_of_the_resulting_obligations_are_listed_when_their_period_overlaps(client, world):
    """On the seeded Customer Zero organisation (adopted obligations with frozen snapshots): for every listed scope with
    an obligation, the snapshots listed are exactly that lineage's snapshots whose period overlaps the change."""
    from app.db import AdminSession
    from app.engines.facts import overlaps
    from app.models import AssuranceSnapshot, ObligationInstance, RuleVersion, Subject
    with AdminSession() as db:
        found = None
        for o in db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == "tenant-zoiko-group",
                                                              ObligationInstance.record_to_seq.is_(None))
                            .order_by(ObligationInstance.lineage_id)).scalars():
            snaps = db.execute(select(AssuranceSnapshot).where(
                AssuranceSnapshot.obligation_lineage_id == o.lineage_id)).scalars().all()
            rv = db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == o.obligation_definition_id,
                                                      RuleVersion.status == "PUBLISHED")).scalars().first()
            s = db.get(Subject, o.subject_id)
            if snaps and rv and rv.required_facts and s.subject_type == "LEGAL_ENTITY":
                found = (o, rv.required_facts[0], snaps)
                break
        assert found, "the seeded fixture has an adopted obligation with a snapshot"
        o, fact, snaps = found
        from app.platform import ensure_utc
        lo = min(ensure_utc(x.period_from) for x in snaps)
    for valid_from, valid_to in ((lo - timedelta(days=3650), None), (lo - timedelta(days=3650), lo)):
        body = _ok(_preview(client, o.subject_id, fact, valid_from, valid_to, actor="actor-cco"))
        for d in body["dependents"]:
            if d["obligation"] is None:
                continue
            with AdminSession() as db:
                expected = sorted((x.record_cutoff_seq, x.snapshot_id) for x in db.execute(select(AssuranceSnapshot).where(
                    AssuranceSnapshot.tenant_id == "tenant-zoiko-group",
                    AssuranceSnapshot.obligation_lineage_id == d["obligation"]["lineage_id"])).scalars()
                    if overlaps(x.period_from, x.period_to, valid_from, valid_to))
            assert sorted((x["record_cutoff_seq"], x["snapshot_id"]) for x in d["assurance_snapshots"]) == expected
        lineage = next(d for d in body["dependents"] if d["obligation"] and d["obligation"]["lineage_id"] == o.lineage_id)
        if valid_to is None:
            assert {x["snapshot_id"] for x in lineage["assurance_snapshots"]} >= {x.snapshot_id for x in snaps}
        else:  # ends where the earliest snapshot period starts: half-open, so none of them overlaps
            assert lineage["assurance_snapshots"] == []


# --------------------------------------------------------------------- the preview against a committed correction
def test_a_correction_that_changes_applicability_touches_exactly_what_the_preview_listed(client, world):
    """Preview, then commit, a CORRECTION of F to False on `two` from 100 days ago. The write closes exactly the revision
    the preview named (the earlier slice stays current); the scopes it re-evaluates are the previewed scopes with a
    recorded evaluation; their new outcomes change (APPLIES -> DOES_NOT_APPLY), which only the committed evaluation
    shows. Every earlier evaluation is unchanged and still replays as recorded (TIM-REQ-007, APP-REQ-012)."""
    earlier = {S["ev"][("two", k, w)] for k in (A, B, C) for w in ("old", "now")}
    stored = _stored(earlier)
    preview = _ok(_preview(client, S["two"], F, ago(100)))
    named = {r["revision_id"] for r in preview["overlapping_revisions"]}
    written = _write(client, CCO_A, S["two"], F, False, ago(100), change_kind="CORRECTION", reason="corrected flag")
    history = _ok(client.get(f"/api/subjects/{S['two']}/facts/history", headers=H(CCO_A)))
    f_rows = [h for h in history if h["fact_key"] == F]
    closed_now = {h["revision_id"] for h in f_rows if h["record_to_seq"] is not None and h["record_to_seq"] ==
                  max(x["record_from_seq"] for x in written["revisions"])}
    assert closed_now == named and len(named) == 1
    still_current = {h["revision_id"] for h in f_rows if h["record_to_seq"] is None}
    assert len(still_current - {r["revision_id"] for r in written["revisions"]}) == 1  # the earlier slice
    reevaluated = {(x["obligation"], x.get("outcome")) for x in written["impact_reevaluations"]}
    assert reevaluated == {(k, "DOES_NOT_APPLY") for k, d in _scopes(preview).items() if d["evaluations"]}
    assert {k for k, _ in reevaluated} == {A, B}
    assert _stored(earlier) == stored  # history preserved: no earlier evaluation rewritten
    for i in earlier:
        assert _replay_status(client, i) == "REPRODUCED"


def test_a_correction_that_keeps_the_outcome_is_previewed_the_same_way(client, world):
    """G corrected to the same value on `two`: the preview lists C exactly as for any change of G (it takes no value and
    predicts nothing); the committed re-evaluation keeps APPLIES; the earlier evaluations are unchanged."""
    earlier = {S["ev"][("two", C, w)] for w in ("old", "now")}
    stored = _stored(earlier)
    preview = _ok(_preview(client, S["two"], G, ago(50)))
    assert sorted(_scopes(preview)) == [C] and _ev_ids(preview) == {S["ev"][("two", C, "now")]}
    written = _write(client, CCO_A, S["two"], G, True, ago(50), change_kind="CORRECTION", reason="re-confirmed")
    assert [(x["obligation"], x.get("outcome")) for x in written["impact_reevaluations"]] == [(C, "APPLIES")]
    assert _stored(earlier) == stored
    for i in earlier:
        assert _replay_status(client, i) == "REPRODUCED"


# ------------------------------------------------------------------------------------- isolation and authorization
def test_other_organisations_and_principals_without_fact_read_receive_nothing(client, world):
    """Organisation B's reader cannot find A's subject (404); an external auditor and a tenant security admin (no fact
    read) and Zoiko content staff are refused (403); A's preview never names B's evaluation of the same definition,
    and B's names only B's."""
    for actor, status in ((CCO_B, 404), (AUDITOR_A, 403), (TSA_A, 403), (AUTHOR, 403)):
        r = _preview(client, S["one"], F, ago(1000), actor=actor)
        assert r.status_code == status, (actor, r.status_code, r.text)
        assert S["ev"][("one", A, "now")] not in r.text
    # Another organisation's subject ID is 404 before any input is checked (QA-REQ-019), even with nothing sent.
    assert client.get(f"/api/subjects/{S['one']}/facts/impact", headers=H(CCO_B)).status_code == 404
    mine = _ok(_preview(client, S["one"], F, ago(1000)))
    assert S["ev"][("other", A, "now")] not in json.dumps(mine)
    theirs = _ok(_preview(client, S["other"], F, ago(1000), actor=CCO_B))
    assert _ev_ids(theirs) == {S["ev"][("other", A, "now")]}
    assert all(d["subject"]["subject_id"] == S["other"] for d in theirs["dependents"])


# ------------------------------------------------------------------------------------------------- invalid inputs
def test_invalid_or_ambiguous_inputs_are_refused_and_write_nothing(client, world):
    """No valid time (never defaulted: ZA-TIM-001 §16), an empty or negative interval, an unparseable or
    non-representable instant, a missing or unknown fact, a fact of another subject type and an unknown subject are
    refused as a fact write refuses them; nothing is written."""
    before = _snapshot_of_state()
    url = f"/api/subjects/{S['one']}/facts/impact"
    cases = [
        ({"fact_key": F}, 422),                                                          # no valid_from
        ({"valid_from": _iso(ago(10))}, 422),                                            # no fact_key
        ({"fact_key": F, "valid_from": _iso(ago(10)), "valid_to": _iso(ago(10))}, 422),  # empty
        ({"fact_key": F, "valid_from": _iso(ago(10)), "valid_to": _iso(ago(20))}, 422),  # negative
        ({"fact_key": F, "valid_from": "not-a-time"}, 422),
        ({"fact_key": F, "valid_from": "0001-01-01T00:00:00+05:00"}, 422),               # no UTC equivalent
        ({"fact_key": f"fci{TAG}.no_such_fact", "valid_from": _iso(ago(10))}, 404),
        ({"fact_key": Y, "valid_from": _iso(ago(10))}, 422),                             # attaches to AI_ASSET
    ]
    for params, status in cases:
        r = client.get(url, headers=H(CCO_A), params=params)
        assert r.status_code == status, (params, r.status_code, r.text)
    r = client.get(f"/api/subjects/s-fci-missing-{TAG}/facts/impact", headers=H(CCO_A),
                   params={"fact_key": F, "valid_from": _iso(ago(10))})
    assert r.status_code == 404, r.text
    assert _snapshot_of_state() == before


# --------------------------------------------------------------------- record time: the echoed watermark (last, as
# these tests publish a rule and write records the earlier ones do not expect)
def _watermark() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _pin_watermark(monkeypatch, known_seq: int) -> None:
    """Answer the preview's watermark read with an earlier one: the window in which another transaction commits between
    that read and the reads after it."""
    import app.routers.applicability as router
    real = router.current_watermark
    monkeypatch.setattr(router, "current_watermark", lambda db: {**real(db), "record_seq": known_seq})


def test_records_written_after_the_echoed_watermark_are_not_in_the_answer(client, world, monkeypatch):
    """TIM-REQ-017 / ZA-TIM-001 "AS_KNOWN_THEN excludes revisions first recorded after K": an answer echoing K holds what
    was known at K. Written after K: an adoption (new determination revision and an obligation), a correction (closing
    the fact revision, new revisions and a re-evaluation) and a rule version for another definition. At K the answer
    shows the revision, evaluation and determination current then, no obligation and no such definition; the same
    preview at the live watermark shows all of them."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-fci-late-{TAG}", f"e-fci-late-{TAG}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name="FCI late Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY",
                       display_name="FCI late Ltd"))
        db.commit()
    (first,) = [r["revision_id"] for r in _write(client, CCO_A, sid, F, True, ago(400))["revisions"]]
    e1 = _evaluate(client, CCO_A, sid, S["odef"][A], NOW)
    d1 = e1["current_determination"]
    known = _watermark()
    # ---- recorded after the watermark
    adopted = _ok(client.post(f"/api/applicability/determinations/{d1['lineage_id']}/attest", headers=H(CCO_A),
                              json={"decision": "ADOPT", "authority_basis": "Board resolution (late probe)"}))
    corrected = _write(client, CCO_A, sid, F, False, ago(100), change_kind="CORRECTION", reason="late correction")
    late = f"FCI_{TAG.upper()}_LATE"
    S["odef"][late] = _publish(client, late, [F], S["requirement"], S["src"])
    assert _watermark() > known
    _pin_watermark(monkeypatch, known)
    then = _ok(_preview(client, sid, F, ago(1000)))
    monkeypatch.undo()
    now = _ok(_preview(client, sid, F, ago(1000)))
    for body in (then, now):
        _assert_contract(body)
        _assert_recorded(body)
    assert then["perspective"]["known_seq"] == known and now["perspective"]["known_seq"] > known
    # fact revisions: at K the one the correction later closed, open as it was then
    assert [(r["revision_id"], r["record_to_seq"]) for r in then["overlapping_revisions"]] == [(first, None)]
    assert {r["revision_id"] for r in now["overlapping_revisions"]} == {r["revision_id"] for r in corrected["revisions"]}
    # evaluations: none recorded after K
    a_then, a_now = _scopes(then)[A], _scopes(now)[A]
    assert [e["evaluation_id"] for e in a_then["evaluations"]] == [e1["evaluation_id"]]
    assert all(e["record_seq"] <= known for d in then["dependents"] for e in d["evaluations"])
    assert len(a_now["evaluations"]) == 2  # with the correction's re-evaluation
    # determination and obligation: the revisions current at K
    assert (a_then["determination"]["revision_id"], a_then["determination"]["state"]) == (d1["revision_id"], "PROPOSED")
    assert a_then["obligation"] is None
    assert a_now["determination"]["revision_id"] != d1["revision_id"]
    assert a_now["obligation"]["lineage_id"] == adopted["obligation"]["lineage_id"]
    # rule versions: a definition whose only version needing the fact was recorded after K is no dependent at K
    assert late not in _scopes(then) and _scopes(now)[late]["rule_versions"]


def test_assurance_snapshots_are_listed_only_once_recorded_by_the_watermark(client, world, monkeypatch):
    """A snapshot is known at K only if its assurance.snapshot_created event was recorded by K (the rule of the dossier's
    historical read; the row has no record column). Of three snapshots of `adopt`'s obligation over the same period, all
    pinned to a cutoff before K: one recorded before K is listed at K and now; one recorded after K (an AS_KNOWN_THEN
    snapshot computed later) only now; one with no creation event never. GET /obligations/{lineage}?as_of_record_seq=K
    lists the same snapshot at K."""
    from app.db import AdminSession
    from app.models import AssuranceSnapshot
    from app.platform import emit, next_record_seq
    obligation = S["adopted"]["obligation"]
    cutoff = _watermark()

    def snapshot(name: str, recorded: bool) -> str:
        with AdminSession() as db:
            x = AssuranceSnapshot(
                tenant_id=TA, entity_id=f"e-fci-adopt-{TAG}", obligation_lineage_id=obligation["lineage_id"],
                obligation_revision_id=obligation["revision_id"], period_from=ago(30), period_to=ago(1),
                record_cutoff_seq=cutoff, perspective="AS_KNOWN_THEN", expression_version_id=f"probe-{name}",
                rollup_engine_version="probe", state="NOT_PROVEN", segments=[], dependency_manifest={},
                dependency_digest="0" * 64, snapshot_hash=secrets.token_hex(32), created_by=CCO_A)
            db.add(x)
            db.flush()
            if recorded:  # as asr.compute_snapshot records it: the event in the snapshot's own transaction
                emit(db, event_type="assurance.snapshot_created", aggregate_type="AssuranceSnapshot",
                     aggregate_id=x.snapshot_id, tenant_id=TA, entity_id=x.entity_id, record_seq=next_record_seq(db),
                     payload={"probe": name})
            db.commit()
            return x.snapshot_id
    before = snapshot("before", True)
    known = _watermark()
    after, unplaced = snapshot("after", True), snapshot("unplaced", False)
    _pin_watermark(monkeypatch, known)
    then = _ok(_preview(client, S["adopt"], F, ago(1000)))
    monkeypatch.undo()
    now = _ok(_preview(client, S["adopt"], F, ago(1000)))
    for body in (then, now):
        _assert_contract(body)
    listed = {name: [x["snapshot_id"] for x in _scopes(body)[A]["assurance_snapshots"]]
              for name, body in (("then", then), ("now", now))}
    assert listed["then"] == [before]
    assert sorted(listed["now"]) == sorted([before, after]) and unplaced not in listed["now"]
    dossier = _ok(client.get(f"/api/obligations/{obligation['lineage_id']}", headers=H(CCO_A),
                             params={"as_of_record_seq": known}))
    assert [x["snapshot_id"] for x in dossier["snapshots"]] == listed["then"]
