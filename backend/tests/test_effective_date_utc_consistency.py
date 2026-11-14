"""Effective-date consistency under Option U: one instant, one legal date, on every path and within one operation.

Owner decision "Option U" (TIM owner and RUL owner; rules.legal_date): an offset-aware effective_at is converted to UTC
and its UTC calendar date is the legal date; RuleVersion validity is [valid_from, valid_to) (TIM-REQ-002).
tests/test_effective_date_utc.py proves each path derives its date from rules.legal_date. This module verifies what is
left of the policy:

  * the explanation the sandbox returns shows the legal date that was compared, as live evaluation and replay record
    it (defect: the sandbox trace showed CONTEXT_REF "2027-01-01T23:30:00-05:00" passing DATE_ON_OR_AFTER 2027-01-02,
    where live evaluation of the same instant records "2027-01-02");
  * one publication decides rule.superseded and re-evaluates its impact on one instant (defect: two clock reads, so a
    release straddling UTC midnight emitted no supersession while its impact already bound the new version);
  * one catalogue trace resolves every definition's in-force version on one legal date (defect: one clock read per
    definition);
  * guards: equivalent spellings of one instant are one evaluation (same key and bundle hash) and replay; UTC midnight
    boundaries for live evaluation, history compare, replay, shadow and the cached catalogue list; the frozen bundle,
    evaluation key and legacy key profile keep their recorded form; the seeded signed releases still verify.
Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"EDC-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-edc-{TAG.lower()}", f"edc-cco-{TAG.lower()}"
EU = "entity.established_in_eu"
SAME_INSTANT = ["2027-01-01T23:30:00-05:00", "2027-01-02T04:30:00Z", "2027-01-02T04:30:00+00:00",
                "2027-01-02T05:30:00+01:00", "2027-01-02T13:30:00+09:00", "2027-01-01T18:30:00-10:00"]
EST = timezone(timedelta(hours=-5))
FD = {EU: {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"}}
S: dict = {}


def _context_rule(threshold: str, t: str = "x") -> dict:
    """ALL(established in the EU, effective date on or after `threshold`)."""
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_on", "operator": "DATE_ON_OR_AFTER", "args": [
            {"node_type": "CONTEXT_REF", "node_id": f"{t}_ctx", "key": "effective_at"},
            {"node_type": "CONST", "node_id": f"{t}_d", "type": "DATE", "value": threshold}]}]}


def _node(trace: dict, node_type: str) -> dict:
    if trace.get("node_type") == node_type:
        return trace
    for c in trace.get("children") or []:
        found = _node(c, node_type)
        if found:
            return found
    return {}


def _clock(*instants: datetime):
    """A datetime class whose now() returns the given instants in turn (the last one from then on), each exactly as
    given (whatever its offset): a clock read by the module it is patched into."""
    seq = list(instants)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return seq.pop(0) if len(seq) > 1 else seq[0]
    return Clock


# ------------------------------------------------------------------------------------------------ pure
@pytest.mark.parametrize("ea", SAME_INSTANT + ["2027-01-02", "20270102", "2026-W53-6", "2027-01-02T23:30:00"])
def test_the_trace_shows_the_legal_date_compared_whatever_the_spelling(ea):
    """The sandbox and golden vectors pass effective_at as written, live evaluation and replay pass its legal date: the
    same instant must give the same trace. CONTEXT_REF displayed the text as written ("2027-01-01T23:30:00-05:00" next
    to DATE_ON_OR_AFTER 2027-01-02 = TRUE); it now displays the legal date compared."""
    from app.engines import rules
    from app.platform import digest
    facts = {EU: {"known": True, "value": True, "revision_id": "r"}}
    ftypes = {EU: "BOOLEAN"}
    as_written = rules.evaluate(_context_rule("2027-01-02"), facts, ftypes, {}, {"effective_at": ea})
    as_live = rules.evaluate(_context_rule("2027-01-02"), facts, ftypes, {}, {"effective_at": rules.legal_date(ea).isoformat()})
    assert _node(as_written["trace"], "CONTEXT_REF")["display_value"] == rules.legal_date(ea).isoformat()
    assert digest(as_written["trace"]) == digest(as_live["trace"]) and as_written["outcome"] == as_live["outcome"]


def test_the_frozen_bundle_and_keys_of_an_instant_are_unchanged():
    """Guard (pinned on the code before this change): the bundle hash and APP §06 evaluation key of an instant do not
    depend on its spelling and are not rewritten; a legacy (date-level) key is recognised by the UTC date of the stored
    instant, never by the date as written west of UTC."""
    from app.engines import applicability as appl
    from app.platform import canonical_json, digest

    def bundle(ea):
        return {"tenant_id": "t", "subject_ref": {"type": "LEGAL_ENTITY", "id": "s"}, "obligation_definition_id": "o",
                "rule_version_id": "r", "rule_ast_hash": "h", "effective_at": ea, "record_cutoff_seq": 41,
                "fact_inputs": [], "unknown_facts": [], "evaluator_build": "za-applicability-evaluator:1.0.0"}
    spellings = [datetime(2027, 1, 1, 23, 30, tzinfo=EST), datetime(2027, 1, 2, 4, 30, tzinfo=timezone.utc)]
    assert {digest(bundle(ea)) for ea in spellings} == {"79706131a04531c169b852d8f34a5ad2d4b1dbf588008af80254dfb3dca1dc6e"}
    assert {appl.evaluation_key(bundle(ea)) for ea in spellings} == \
        {"464eeee7e63152d364d36b89df67ab4b89bca1568d48072bc4f5f57281f1158e"}
    stored = json.loads(canonical_json(bundle(spellings[0])))
    assert stored["effective_at"] == "2027-01-02T04:30:00Z"

    def legacy(effective_date):
        return digest({k: stored[k] for k in appl.LEGACY_EVALUATION_KEY_FIELDS} | {"effective_date": effective_date})
    profile = {d: appl.evaluation_key_profile(SimpleNamespace(input_bundle=stored, evaluation_key=legacy(d)))
               for d in ("2027-01-02", "2027-01-01")}
    assert profile == {"2027-01-02": "LEGACY_EFFECTIVE_DATE", "2027-01-01": "UNRECOGNISED"}


# ------------------------------------------------------------------------------------------------ the API
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Effective date consistency Instrument", jurisdiction="EU",
                          authority="Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="EDC CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Effective date consistency Act", "locator": f"OJ L {TAG}/edc", "instrument_identifier": INSTR,
        "raw_text": "Institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"EDC-{TAG}-REQ",
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
             "regulated_object": "report", "timing": "annual", "normalized_meaning": "Report annually.",
             "evidence_expectation": "Report"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["subject"] = _subject("main")
    S["ctx_odef"] = _odef()  # effective date on or after 2027-01-02, in force from 2024-01-01
    S["ctx_draft"], S["ctx_pub"] = _published(client, S["ctx_odef"], "2027-01-02", "2024-01-01", None, "2027-03-01")
    S["window_odef"] = _odef()  # in force on [2027-01-02, 2027-01-03) only
    _, S["window_pub"] = _published(client, S["window_odef"], "2000-01-01", "2027-01-02", "2027-01-03", "2027-01-02")
    return True


def _subject(name: str) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    r = S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": EU, "value": True, "valid_from": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()})
    assert r.status_code == 200, r.text
    return sid


def _odef() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"EDC_{TAG}_{secrets.token_hex(2)}", title="Effective date consistency obligation",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _body(odef, threshold, valid_from, valid_to, vector_at) -> dict:
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]

    def vec(vid, cls, eu, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": vector_at, "facts": {EU: eu},
                "expected_outcome": out}
    return {"obligation_definition_id": odef, "authoring_mode": "AST", "ast": _context_rule(threshold, t),
            "valid_from": valid_from, "valid_to": valid_to, "change_class": "EXECUTABLE_OUTCOME",
            "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")],
            "source_mappings": [{"source_mapping_id": f"sm_{t}", "requirement_version_id": S["rv"]["requirement_version_id"],
                                 "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                                 "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT",
                                 "reviewer": LEGAL}]}


def _shadowed(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _published(client, odef, threshold, valid_from, valid_to, vector_at):
    did = _shadowed(client, _body(odef, threshold, valid_from, valid_to, vector_at))
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"edc-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return did, r.json()


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _evaluate(client, odef, ea, cutoff=None, sid=None):
    body = {"subject_id": sid or S["subject"], "obligation_definition_id": odef, "effective_at": ea}
    if cutoff is not None:
        body["record_cutoff_seq"] = cutoff
    return client.post("/api/applicability/evaluations", headers=H(CCO), json=body)


def test_sandbox_and_live_evaluation_explain_one_instant_with_one_legal_date(client, world):
    """The sandbox trace showed the instant as written beside a comparison made on its UTC date."""
    ea = "2027-01-01T23:30:00-05:00"
    sb = client.post(f"/api/rules/drafts/{S['ctx_draft']}/sandbox", headers=H(AUTHOR),
                     json={"facts": {EU: True}, "effective_at": ea})
    live = _evaluate(client, S["ctx_odef"], ea)
    assert sb.status_code == 200 and live.status_code == 200, (sb.text, live.text)
    sb_ctx, live_ctx = _node(sb.json()["trace"], "CONTEXT_REF"), _node(live.json()["trace"], "CONTEXT_REF")
    assert sb_ctx["display_value"] == live_ctx["display_value"] == "2027-01-02", (sb_ctx, live_ctx)
    assert sb.json()["outcome"] == live.json()["outcome"] == sb.json()["vector_probe"]["actual"] == "APPLIES"


def test_equivalent_spellings_of_one_instant_are_one_evaluation_that_replays(client, world):
    """Guard: at one record cutoff, every spelling of one instant is the same frozen input bundle and APP §06 key, so
    they collapse to one evaluation, stored at the UTC instant, which replays (REPRODUCED)."""
    cutoff = _seq()
    rows = [_evaluate(client, S["ctx_odef"], ea, cutoff) for ea in SAME_INSTANT]
    assert all(r.status_code == 200 for r in rows), [r.text for r in rows]
    views = [r.json() for r in rows]
    assert len({(v["evaluation_id"], v["input_bundle_hash"], v["evaluation_key"]) for v in views}) == 1
    v = client.get(f"/api/applicability/evaluations/{views[0]['evaluation_id']}", headers=H(CCO)).json()
    assert (v["outcome"], v["evaluation_key_profile"], v["replay"]["status"]) == ("APPLIES", "APP_001_06", "REPRODUCED")
    assert v["input_bundle"]["effective_at"] == "2027-01-02T04:30:00Z"
    assert datetime.fromisoformat(v["effective_at"].replace("Z", "+00:00")) == datetime(2027, 1, 2, 4, 30, tzinfo=timezone.utc)


# Instants either side of the UTC midnights that bound [2027-01-02, 2027-01-03), in several offsets.
BOUNDARY = [("2027-01-01T23:59:59.999999Z", False), ("2027-01-02T00:00:00Z", True), ("2027-01-01T19:00:00-05:00", True),
            ("2027-01-01T18:59:59.999999-05:00", False), ("2027-01-02T23:59:59.999999+00:00", True),
            ("2027-01-03T00:59:59+01:00", True), ("2027-01-02T19:00:00-05:00", False), ("2027-01-03T00:00:00Z", False)]


@pytest.mark.parametrize("ea, in_force", BOUNDARY)
def test_live_history_compare_and_replay_agree_at_each_utc_midnight(client, world, ea, in_force):
    """Guard: live evaluation, the history compare (persist=False) and the replay of the stored evaluation all bind
    the version valid on [2027-01-02, 2027-01-03) exactly for the instants whose UTC date is 2027-01-02."""
    cutoff = _seq()
    live = _evaluate(client, S["window_odef"], ea, cutoff)
    cmp = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": S["subject"], "obligation_definition_id": S["window_odef"], "valid_at": ea,
        "left_seq": cutoff, "right_seq": cutoff})
    assert cmp.status_code == 200, cmp.text
    rv_id = S["window_pub"]["rule_version"]["rule_version_id"]
    if in_force:
        assert live.status_code == 200 and live.json()["rule_version_id"] == rv_id, live.text
        assert cmp.json()["left"]["rule_version_id"] == cmp.json()["right"]["rule_version_id"] == rv_id
        assert cmp.json()["left"]["outcome"] == live.json()["outcome"] == "APPLIES"
        replay = client.get(f"/api/applicability/evaluations/{live.json()['evaluation_id']}", headers=H(CCO)).json()["replay"]
        assert replay["status"] == "REPRODUCED", replay
    else:
        assert live.status_code == 409 and live.json()["detail"]["error"] == "RULE_NOT_EFFECTIVE", live.text
        assert cmp.json()["left"]["error"]["error"] == cmp.json()["right"]["error"]["error"] == "RULE_NOT_EFFECTIVE"


@pytest.mark.parametrize("instant, applies", [
    (datetime(2027, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc), False),
    (datetime(2027, 1, 1, 19, 0, tzinfo=EST), True),  # 2027-01-02T00:00Z, read on a clock that reports UTC-5
    (datetime(2027, 1, 2, 0, 0, tzinfo=timezone.utc), True)], ids=["before", "at-est", "at-utc"])
def test_shadow_evaluates_on_the_utc_date_of_its_instant_at_midnight(client, world, monkeypatch, instant, applies):
    """Guard: the shadow run reads one instant and evaluates the candidate on its UTC date, whatever offset the clock
    reports: DATE_ON_OR_AFTER 2027-01-02 holds from 2027-01-02T00:00Z and not a microsecond before."""
    from app.engines import applicability as appl
    did = _shadowed(client, _body(_odef(), "2027-01-02", "2024-01-01", None, "2027-06-01"))
    monkeypatch.setattr(appl, "datetime", _clock(instant))
    rep = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert rep.status_code == 200, rep.text
    to_applies = sum(n for k, n in rep.json()["transition_buckets"].items() if k.endswith("->APPLIES"))
    assert (to_applies >= 1) is applies and rep.json()["execution_errors"] == 0, rep.json()


def test_one_publication_decides_supersession_and_impact_on_one_instant(client, world, monkeypatch):
    """A release straddling UTC midnight: the supersession decision read the clock before midnight (the new version,
    effective from 2099-01-02, not yet in force: no rule.superseded) and every impact re-evaluation read it again after
    midnight (bound the new version). Both now come from one instant, so the event and the impact record agree."""
    from app.db import AdminSession
    from app.engines import catalog
    from app.models import OutboxEvent
    from app.routers import rules as rules_router
    odef = _odef()
    _, v1 = _published(client, odef, "2000-01-01", "2024-01-01", None, "2027-03-01")
    sid = _subject(f"pub-{secrets.token_hex(2)}")
    first = _evaluate(client, odef, None, sid=sid)
    assert first.status_code == 200 and first.json()["rule_version_id"] == v1["rule_version"]["rule_version_id"], first.text
    did = _shadowed(client, _body(odef, "2000-01-01", "2099-01-02", None, "2099-06-01"))
    clock = _clock(datetime(2099, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc), datetime(2099, 1, 2, tzinfo=timezone.utc))
    monkeypatch.setattr(rules_router, "datetime", clock)
    monkeypatch.setattr(catalog, "datetime", clock)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"edc-{secrets.token_hex(4)}", "changelog": "x"})
    monkeypatch.undo()
    assert r.status_code == 200, r.text
    impact, v2 = r.json()["impact"], r.json()["rule_version"]["rule_version_id"]
    with AdminSession() as db:
        superseded = [e for e in db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "rule.superseded"))
                      .scalars() if e.payload.get("obligation_definition_id") == odef]
    impact_bound_new = impact["evaluated"] > impact["not_yet_effective"]
    assert impact["evaluated"] == 1 and bool(superseded) is impact_bound_new, (impact, superseded)
    assert not superseded and impact["not_yet_effective"] == 1  # the instant read before midnight decides both
    latest = client.get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": sid}).json()[0]
    assert (latest["trigger"], latest["rule_version_id"]) == ("RULE_CHANGED", v1["rule_version"]["rule_version_id"])
    assert latest["rule_version_id"] != v2
    assert datetime.fromisoformat(latest["effective_at"].replace("Z", "+00:00")) == \
        datetime(2099, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_one_catalogue_trace_resolves_every_definition_on_one_legal_date(client, world, monkeypatch):
    """The requirement and source traces resolved each definition's in-force version with its own clock read, so one
    response could mix two legal dates across a UTC midnight. One read per response now."""
    from app.engines import catalog
    days = iter(date(2027, 1, 2) + timedelta(days=n) for n in range(1000))
    monkeypatch.setattr(catalog, "_today", lambda: next(days))
    used: list = []
    real = catalog.effective_rule_version

    def recording(db, odef_id, on=None):
        used.append(on)
        return real(db, odef_id, on)
    monkeypatch.setattr(catalog, "effective_rule_version", recording)
    for path in (f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/trace",
                 f"/api/catalog/sources/{S['source']['source_id']}/trace"):
        used.clear()
        r = client.get(path, headers=H(AUTHOR))
        assert r.status_code == 200, r.text
        rows = r.json()["obligation_definitions"] if "obligation_definitions" in r.json() else \
            [d for q in r.json()["requirements"] for d in q["obligation_definitions"]]
        assert len(rows) >= 2 and len(used) >= 2, (path, len(rows))
        assert len(set(used)) == 1 and None not in used, (path, used)
        on = used[0].isoformat()
        assert {d["effective_rule_version"]["effective_on"] for d in rows if d["effective_rule_version"]} <= {on}
        window = next(d for d in rows if d["obligation_definition_id"] == S["window_odef"])
        assert (window["effective_rule_version"] is not None) is (on == "2027-01-02"), (path, window)


@pytest.mark.parametrize("instant, in_force", [
    (datetime(2027, 1, 1, 18, 59, 59, 999999, tzinfo=EST), False),  # 2027-01-01T23:59:59.999999Z
    (datetime(2027, 1, 1, 19, 0, tzinfo=EST), True)], ids=["before", "at"])  # 2027-01-02T00:00Z
def test_the_cached_catalogue_list_selects_on_the_utc_date_at_midnight(client, world, monkeypatch, instant, in_force):
    """Guard: the catalogue list (and its cache key) takes today's legal date from the UTC date of the clock's instant,
    even when the clock reports it in another offset (whose written date is the day before); the cached and uncached
    lists are equal."""
    from app import redis_client
    from app.engines import catalog
    from test_redis_cache import MemoryRedis
    monkeypatch.setattr(catalog, "datetime", _clock(instant))
    on = catalog._today()
    assert on == instant.astimezone(timezone.utc).date()

    def listed():
        r = client.get("/api/catalog/obligation-definitions", headers=H(AUTHOR))
        assert r.status_code == 200, r.text
        return next(d for d in r.json() if d["obligation_definition_id"] == S["window_odef"])
    monkeypatch.delenv("ZA_CATALOG_CACHE", raising=False)
    direct = listed()
    expected = S["window_pub"]["rule_version"]["rule_version_id"] if in_force else None
    assert (direct["effective_rule_version"] or {}).get("rule_version_id") == expected, direct
    fake = MemoryRedis()
    redis_client.manager.use_client(fake)
    try:
        monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
        assert listed() == direct and listed() == direct  # built once, then served from the entry for this date
        assert [k for k in fake.keys("*") if f":catalog:obligation_definitions.{on.isoformat()}:" in k]
    finally:
        redis_client.manager.close()


def test_existing_signed_releases_still_verify(client, world):
    """Guard: the seeded signed releases (made before this change) and the releases this module published verify with
    an intact manifest and a valid signature; nothing here re-signs or rewrites a release."""
    releases = client.get("/api/catalog/releases", headers=H(AUTHOR)).json()
    seeded = [r["content_release_id"] for r in releases if r["label"].startswith("CR-2026-")]
    ours = [S["ctx_pub"]["content_release"]["content_release_id"], S["window_pub"]["content_release"]["content_release_id"]]
    assert len(seeded) == 4
    for cr_id in seeded + ours:
        rep = client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(AUTHOR)).json()
        assert (rep["integrity"], rep["signature"]["status"]) == ("INTACT", "VALID"), (cr_id, rep)
        assert not [c for c in rep["checks"] if c["status"] == "FAIL"], (cr_id, rep["checks"])
