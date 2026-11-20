"""Regulatory change "view diff": GET /catalog/sources/{source_id}/diff (read-only).

Controlling text:
  * ZA-PRD-001 §13 WF-01 Regulatory Change Inbox: "Actions: open source, view diff, open impact, assign review".
  * ZA-PRD-001 FR-REG-005: "Source-rights metadata controls what text may be stored, displayed, transformed or
    redistributed" (the display rule already applied by GET /catalog/sources: REFERENCE_ONLY text is withheld).
  * ZA-DOM-001 §4 / ZA-DAT-001 §05: a source snapshot is identified by its fingerprint over the stored text; a source's
    snapshots share authority, jurisdiction and authoritative locator (catalog.source_versions, the chain the inbox
    names previous_source_id from); a requirement's stable identity is its requirement_key within the instrument.

Pinned here: a snapshot is compared with the snapshot immediately before it in that chain; the line diff reproduces
the later text exactly from the earlier one and exists exactly when the texts differ (fingerprints differ); no text of
either snapshot is shown when either side is withheld by its rights, not stored, or no longer matches its fingerprint;
requirement versions grounded in the two snapshots are grouped by requirement key; the read writes nothing and is
authorized like the source record itself.
"""
from __future__ import annotations

import json
import secrets

import pytest
from sqlalchemy import select, update

from conftest import H, add_principal

TAG = secrets.token_hex(3)
INSTR = f"SSD-{TAG.upper()}-REG"
AUTHOR, LEGAL, CCO, AUDITOR = "actor-content-analyst", "actor-content-legal", "actor-cco", "actor-ext-auditor"
TSA = f"ssd-tsa-{TAG}"
# As stored: ingestion strips leading and trailing whitespace (SourceIn, str_strip_whitespace), so each text ends
# without a line terminator; the unchanged last line keeps the expected hunks free of end-of-text effects.
T1 = "Article 1\nEntities shall keep a register.\nArticle 2\nReports are due annually.\nEnd of text."
T2 = ("Article 1\nEntities shall keep a complete register.\nArticle 2\nReports are due annually.\n"
      "Article 3\nRecords are kept for five years.\nEnd of text.")
T3 = "Article 1\nEntities shall keep a complete register.\nArticle 3\nRecords are kept for five years.\nEnd of text."
S: dict = {}


def _ingest(client, locator: str, text: str, rights: str = "OFFICIAL_REUSE_PERMITTED") -> str:
    r = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "SSD Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": f"Diff Act {locator}", "locator": locator, "rights_class": rights, "raw_text": text,
        "instrument_identifier": INSTR})
    assert r.status_code == 200, r.text
    return r.json()["source_id"]


def _insert_snapshot(of: str, text: str | None, fingerprint_text: str) -> str:
    """A snapshot of the same source written below the API: an identical re-capture (which the API refuses as a
    duplicate) or one whose text is not stored."""
    from app.db import AdminSession
    from app.models import RegulatorySource, new_id
    from app.platform import next_record_seq, sha256_hex
    with AdminSession() as db:
        base = db.get(RegulatorySource, of)
        row = {c.name: getattr(base, c.name) for c in RegulatorySource.__table__.columns}
        sid = new_id()
        row.update(source_id=sid, raw_text=text, fingerprint=sha256_hex(fingerprint_text),
                   record_seq=next_record_seq(db))
        db.add(RegulatorySource(**row))
        db.commit()
        return sid


def _requirement(client, source_id: str, key: str) -> str:
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source_id, "instrument_identifier": INSTR, "requirement_key": key, "title": f"Requirement {key}",
        "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    return r.json()["requirement_version_id"]


@pytest.fixture(scope="module")
def world(client):
    """Chain `main`: S1 -> S2 -> S3 (T1, T2, T3), with requirement keys K1 (grounded in S1 and S2), K2 (S1 only) and
    K3 (S2 only). Chains for the fail-closed cases: `ref` (second snapshot REFERENCE_ONLY), `tamper` (second snapshot's
    stored text altered), `nostore` (second snapshot without text), `same` (identical re-capture) and `eol` (the texts
    differ only by a final line terminator)."""
    from app.db import AdminSession
    from app.models import Instrument
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Diff Instrument", jurisdiction="EU", authority="SSD Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        add_principal(db, actor_id=TSA, tenant_id="tenant-zoiko-group", actor_type="HUMAN", display_name=TSA,
                      roles=["TENANT_SECURITY_ADMIN"])
        db.commit()
    loc = {k: f"OJ L {TAG}/{k}" for k in ("main", "ref", "tamper", "nostore", "same", "eol")}
    S["s1"] = _ingest(client, loc["main"], T1)
    S["k"] = {k: f"SSD-{TAG}-{k}" for k in ("K1", "K2", "K3")}
    S["rv"] = {("K1", "s1"): _requirement(client, S["s1"], S["k"]["K1"]),
               ("K2", "s1"): _requirement(client, S["s1"], S["k"]["K2"])}
    S["s2"] = _ingest(client, loc["main"], T2)
    S["rv"][("K1", "s2")] = _requirement(client, S["s2"], S["k"]["K1"])
    S["rv"][("K3", "s2")] = _requirement(client, S["s2"], S["k"]["K3"])
    S["s3"] = _ingest(client, loc["main"], T3)
    S["ref"] = [_ingest(client, loc["ref"], "Open text, reuse permitted.\n"),
                _ingest(client, loc["ref"], "Licensed text, reference only.\n", rights="REFERENCE_ONLY"),
                _ingest(client, loc["ref"], "Open text again, reuse permitted.\n")]
    S["tamper"] = [_ingest(client, loc["tamper"], "Original wording one.\n"),
                   _ingest(client, loc["tamper"], "Original wording two.\n")]
    with AdminSession() as db:
        from app.models import RegulatorySource
        db.execute(update(RegulatorySource).where(RegulatorySource.source_id == S["tamper"][1])
                   .values(raw_text="Substituted wording after capture.\n"))
        db.commit()
    S["nostore"] = [_ingest(client, loc["nostore"], "Stored first wording.\n")]
    S["nostore"].append(_insert_snapshot(S["nostore"][0], None, "Second wording, held by reference only.\n"))
    S["same"] = [_ingest(client, loc["same"], "Unchanged wording.")]
    S["same"].append(_insert_snapshot(S["same"][0], "Unchanged wording.", "Unchanged wording."))
    # The API strips a final terminator, so the capture that keeps one is written below it.
    S["eol"] = [_ingest(client, loc["eol"], "Line A\nLine B")]
    S["eol"].append(_insert_snapshot(S["eol"][0], "Line A\nLine B\n", "Line A\nLine B\n"))
    return True


def _diff(client, sid, actor=CCO):
    return client.get(f"/api/catalog/sources/{sid}/diff", headers=H(actor))


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _rebuild(previous_text: str, hunks: list[dict]) -> str:
    """The later text, rebuilt from the earlier one and the hunks alone."""
    a, out, cursor = previous_text.splitlines(keepends=True), [], 0
    for h in hunks:
        assert h["previous"]["lines"] == a[h["previous"]["start"]:h["previous"]["end"]]
        out += a[cursor:h["previous"]["start"]] + h["current"]["lines"]
        cursor = h["previous"]["end"]
    return "".join(out + a[cursor:])


# ------------------------------------------------------------------------------------------------------------ diff
def test_each_snapshot_is_compared_with_the_one_before_it(client, world):
    first, second, third = (_ok(_diff(client, S[k])) for k in ("s1", "s2", "s3"))
    assert first["previous"] is None and first["text_diff"] is None and first["fingerprint_changed"] is None
    assert (second["previous"]["source_id"], third["previous"]["source_id"]) == (S["s1"], S["s2"])
    assert [d["source"]["snapshot_number"] for d in (first, second, third)] == [1, 2, 3]
    assert {d["snapshot_count"] for d in (first, second, third)} == {3}
    # The same chain and order as the snapshot list of the source.
    versions = _ok(client.get(f"/api/catalog/sources/{S['s3']}/versions", headers=H(CCO)))["snapshots"]
    assert [v["source_id"] for v in versions] == [S["s1"], S["s2"], S["s3"]]
    for d in (second, third):
        assert d["fingerprint_changed"] is True
        for side in (d["source"], d["previous"]):
            assert (side["text_stored"], side["text_displayable"], side["fingerprint_intact"]) == (True, True, True)


def test_the_line_diff_is_exact_and_rebuilds_the_later_text(client, world):
    second, third = _ok(_diff(client, S["s2"])), _ok(_diff(client, S["s3"]))
    assert second["text_diff"] == {"previous_line_count": 5, "current_line_count": 7, "hunks": [
        {"op": "replace", "previous": {"start": 1, "end": 2, "lines": ["Entities shall keep a register.\n"]},
         "current": {"start": 1, "end": 2, "lines": ["Entities shall keep a complete register.\n"]}},
        {"op": "insert", "previous": {"start": 4, "end": 4, "lines": []},
         "current": {"start": 4, "end": 6, "lines": ["Article 3\n", "Records are kept for five years.\n"]}}]}
    assert third["text_diff"]["hunks"] == [
        {"op": "delete", "previous": {"start": 2, "end": 4, "lines": ["Article 2\n", "Reports are due annually.\n"]},
         "current": {"start": 2, "end": 2, "lines": []}}]
    assert _rebuild(T1, second["text_diff"]["hunks"]) == T2 and _rebuild(T2, third["text_diff"]["hunks"]) == T3
    assert _ok(_diff(client, S["s2"])) == second  # deterministic


def test_a_hunk_exists_exactly_when_the_texts_differ(client, world):
    same = _ok(_diff(client, S["same"][1]))
    assert same["fingerprint_changed"] is False and same["text_diff"]["hunks"] == []
    eol = _ok(_diff(client, S["eol"][1]))  # only the final line terminator differs
    assert eol["fingerprint_changed"] is True
    assert eol["text_diff"]["hunks"] == [{"op": "replace", "previous": {"start": 1, "end": 2, "lines": ["Line B"]},
                                          "current": {"start": 1, "end": 2, "lines": ["Line B\n"]}}]
    assert _rebuild("Line A\nLine B", eol["text_diff"]["hunks"]) == "Line A\nLine B\n"


# ------------------------------------------------------------------------------------------------------------ fail closed
@pytest.mark.parametrize("chain, index, bad_side, flag, withheld", [
    ("ref", 1, "source", "text_displayable", ["Open text, reuse permitted.", "Licensed text, reference only."]),
    ("ref", 2, "previous", "text_displayable", ["Licensed text, reference only.", "Open text again, reuse permitted."]),
    ("tamper", 1, "source", "fingerprint_intact", ["Original wording one.", "Substituted wording after capture."]),
    ("nostore", 1, "source", "text_stored", ["Stored first wording."]),
])
def test_no_text_is_shown_when_either_side_is_withheld_unstored_or_altered(client, world, chain, index, bad_side, flag,
                                                                           withheld):
    body = _ok(_diff(client, S[chain][index]))
    assert body["text_diff"] is None and body["note"]
    good_side = "previous" if bad_side == "source" else "source"
    assert body[bad_side][flag] is False and body[good_side][flag] is True
    dumped = json.dumps(body)
    for fragment in withheld:  # neither side's text, not even the side that may be shown on its own
        assert fragment not in dumped, fragment
    assert "raw_text" not in dumped


# ------------------------------------------------------------------------------------------------------------ requirements
def test_requirement_versions_of_both_snapshots_are_grouped_by_requirement_key(client, world):
    body = _ok(_diff(client, S["s2"]))
    k = S["k"]
    assert [g["requirement_key"] for g in body["requirements"]] == sorted(k.values())
    by_key = {g["requirement_key"]: g for g in body["requirements"]}
    ids = {key: ([r["requirement_version_id"] for r in g["previous"]], [r["requirement_version_id"] for r in g["current"]])
           for key, g in by_key.items()}
    assert ids == {k["K1"]: ([S["rv"][("K1", "s1")]], [S["rv"][("K1", "s2")]]),
                   k["K2"]: ([S["rv"][("K2", "s1")]], []),
                   k["K3"]: ([], [S["rv"][("K3", "s2")]])}
    k1 = by_key[k["K1"]]
    assert (k1["previous"][0]["version_number"], k1["current"][0]["version_number"]) == (1, 2)
    assert all(r["provision_count"] == 1 for g in body["requirements"] for r in g["previous"] + g["current"])
    # The third snapshot grounds nothing, the second's versions are its previous side; the first has no previous side.
    third = _ok(_diff(client, S["s3"]))
    assert {g["requirement_key"] for g in third["requirements"]} == {k["K1"], k["K3"]}
    assert all(not g["current"] for g in third["requirements"])
    first = _ok(_diff(client, S["s1"]))
    assert all(not g["previous"] for g in first["requirements"])


# ------------------------------------------------------------------------------------------------------------ access
def test_readers_are_the_source_records_and_nothing_is_written(client, world):
    from test_authorization import db_fingerprint, denials
    before = db_fingerprint()
    for actor in (CCO, AUDITOR, AUTHOR, LEGAL):  # organisation readers, the auditor allowlist and content operations
        assert _diff(client, S["s2"], actor).status_code == client.get(
            f"/api/catalog/sources/{S['s2']}", headers=H(actor)).status_code == 200, actor
    n = denials(TSA)
    assert _diff(client, S["s2"], TSA).status_code == 403  # a role administrator reads no catalogue content
    assert denials(TSA) == n + 1
    assert client.get(f"/api/catalog/sources/{S['s2']}/diff").status_code == 401
    assert _diff(client, "no-such-source").status_code == 404
    assert db_fingerprint() == before
