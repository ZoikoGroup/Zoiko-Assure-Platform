"""Content-release signature verification (roadmap A9b; docs/regulatory-release-signature-verification-report.md).

Requirements exercised: ZA-RUL-001 §12 (the release signs the canonical manifest; algorithm identified), §02 (the
release packages its RequirementVersions), §18 RUL_SIGNATURE_INVALID; RUL-REQ-027; APP-REQ-006 ("signature-valid");
ZA-DAT-001 §05 ("Cells verify before activation"), DAT-REQ-012; PRD FR-REG-001; SEC-REQ-026/048/049; ZA-SEC-001 §14
(key separation) and §19 (key/signing records: key ID/purpose, caller, operation, result, never key bytes); ADR C-07.

Tampering is done below the application by the table owner (tests/test_rule_integrity.py `_tampered`, which also
lifts the 0004 immutability trigger on PostgreSQL) and is always restored, so every test ends with an intact release.
"""
from __future__ import annotations

import json
import secrets
from contextlib import contextmanager

import pytest
from sqlalchemy import delete, insert, select

from conftest import H, add_principal
from test_authorization import denials
from test_regulatory_traceability import _publish, _requirement, _reviewed_draft, _source
from test_rule_integrity import _tampered

TAG = secrets.token_hex(3).upper()
INSTR = f"RSV-{TAG}"
AUTHOR, LEGAL, RELEASE = "actor-content-analyst", "actor-content-legal", "actor-release"
TENANT, TB, B_CCO, SUPER = "tenant-zoiko-group", "tenant-rsv-b", "rsv-b-cco", "rsv-super"


@pytest.fixture(scope="module")
def release(client):
    """A release published through every gate, binding one verified requirement version and its source snapshot."""
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, Instrument, LegalEntity, ObligationDefinition, Subject, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Signature verification probe", jurisdiction="EU",
                          authority="Probe Authority", coverage_mode="ENCODED"))
        if db.get(Tenant, TB) is None:
            db.add(Tenant(tenant_id=TB, legal_name="RSV Probe B Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=f"entity-{TB}", tenant_id=TB, legal_name="RSV Probe B Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=f"subj-{TB}"))
            db.add(Subject(subject_id=f"subj-{TB}", tenant_id=TB, entity_id=f"entity-{TB}", subject_type="LEGAL_ENTITY",
                           display_name="RSV Probe B Ltd"))
        for aid, tid, roles in ((B_CCO, TB, ["CCO"]), (SUPER, None, ["SUPER_ADMIN"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    s = _source(client, "Credit institutions shall keep a register of ICT arrangements and shall review it.",
                instrument=INSTR, locator=f"OJ L {TAG}/sig").json()
    v = _requirement(client, s["source_id"], f"RSV-{TAG}-REQ", instrument=INSTR).json()
    assert client.post(f"/api/catalog/requirements/{v['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RSV_{TAG}", title="Signature verification probe obligation",
                                 instrument_id=inst.instrument_id, provision_ids=[p["provision_id"] for p in v["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED")
        db.add(o)
        db.commit()
        odef_id = o.obligation_definition_id
    did = _reviewed_draft(client, odef_id, "rsv", v, s)
    r = _publish(client, did, f"rsv-{TAG}")
    assert r.status_code == 200, r.text
    return {"cr": r.json()["content_release"], "rule": r.json()["rule_version"], "rv": v, "source": s}


def _verify(client, cr_id, actor=AUTHOR):
    r = client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()


def _status(report) -> dict:
    """check -> status (each check name occurs once for a single-binding release)."""
    out = {}
    for c in report["checks"]:
        assert c["check"] not in out, c["check"]
        out[c["check"]] = c["status"]
    return out


def _failed(report) -> set:
    return {c["check"] for c in report["checks"] if c["status"] == "FAIL"}


def _events(cr_id, event_type="security.content_signature_failed"):
    from app.db import AdminSession
    from app.models import OutboxEvent
    with AdminSession() as db:
        return db.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type,
                                                    OutboxEvent.aggregate_id == cr_id)
                          .order_by(OutboxEvent.record_seq)).scalars().all()


def _audits(cr_id):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        return db.execute(select(AuditEvent).where(AuditEvent.action == "content.verify", AuditEvent.object_id == cr_id)
                          .order_by(AuditEvent.record_seq, AuditEvent.id)).scalars().all()


INTACT_UNVERIFIABLE = {"SIGNER_TRUST"}  # the only undecided check for an intact, bound, fully signed release


# ------------------------------------------------------------------------------------------------ the intact case
def test_an_intact_release_is_unverifiable_only_for_want_of_a_trust_anchor(client, release):
    cr = release["cr"]
    rep = _verify(client, cr["content_release_id"])
    st = _status(rep)
    assert (rep["outcome"], rep["integrity"]) == ("UNVERIFIABLE", "INTACT")
    assert {k for k, v in st.items() if v != "PASS"} == INTACT_UNVERIFIABLE and st["SIGNER_TRUST"] == "UNVERIFIABLE"
    for check in ("MANIFEST_HASH", "MANIFEST_MATCHES_RELEASE_RECORD", "SIGNATURE", "RULE_VERSION_INTEGRITY",
                  "RELEASE_COVERS_RULE_VERSIONS", "REQUIREMENT_BINDING", "REQUIREMENT_VERSION_IDENTITY",
                  "REQUIREMENT_CONTENT_DIGEST", "PROVISIONS_PRESENT", "SOURCE_FINGERPRINT", "SOURCE_CONTENT"):
        assert st[check] == "PASS", check
    sig = rep["signature"]
    assert (sig["status"], sig["algorithm"], sig["key_id"]) == ("VALID", "ed25519", cr["signer_key_id"])
    assert rep["trust"]["status"] == "NOT_ESTABLISHED" and "C-07" in rep["trust"]["reason"]
    assert rep["summary"] == {"pass": len(st) - 1, "fail": 0, "unverifiable": 1}
    # The pre-A9b fields keep their meaning for existing consumers.
    assert rep["manifest_hash_ok"] is True and rep["signature_ok"] is True and rep["signer_key_id"] == cr["signer_key_id"]
    # Public material only; never private key bytes.
    assert rep["public_key_pem"].startswith("-----BEGIN PUBLIC KEY-----")
    assert "PRIVATE" not in json.dumps(rep)


def test_releases_made_before_requirement_binding_verify_as_unverifiable_not_valid(client, release):
    seeded = [r for r in client.get("/api/catalog/releases", headers=H(AUTHOR)).json() if r["label"].startswith("CR-2026-")]
    assert len(seeded) == 4
    for r in seeded:
        rep = _verify(client, r["content_release_id"])
        st = _status(rep)
        assert (rep["outcome"], rep["integrity"], rep["signature"]["status"]) == ("UNVERIFIABLE", "INTACT", "VALID"), r["label"]
        assert st["REQUIREMENT_BINDING"] == "UNVERIFIABLE" and not _failed(rep)


def test_repeated_verification_is_deterministic_and_a_result_is_recorded_once_per_change(client, release):
    """Audit finding F-15: a repeated identical read writes nothing; a result new to the caller is recorded."""
    cr_id = release["cr"]["content_release_id"]
    probe = "actor-ext-auditor"  # a reader that has not verified this release yet
    before_audits, before_events = len(_audits(cr_id)), len(_events(cr_id))
    responses = [client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(probe)) for _ in range(5)]
    assert all(r.status_code == 200 for r in responses)
    assert len({json.dumps(r.json(), sort_keys=True) for r in responses}) == 1  # deterministic body
    assert [r.headers["Za-Verification-Recorded"] for r in responses] == ["true"] + ["false"] * 4
    new = _audits(cr_id)[before_audits:]
    assert [(a.new_state, a.actor_id) for a in new] == [("UNVERIFIABLE", probe)]  # 5 reads, 1 record
    # Another principal's first verification is its own record, in its own trail.
    assert _verify(client, cr_id) == responses[0].json()
    # ZA-SEC-001 §18 key/signing record: key ID and purpose, operation, result; never key bytes.
    reason = new[0].reason
    assert f"key_id={release['cr']['signer_key_id']}" in reason and "purpose=content_release" in reason
    assert "operation=verify" in reason and "result=UNVERIFIABLE" in reason and "BEGIN" not in reason
    assert len(_events(cr_id)) == before_events  # no security signal for an undecided trust anchor


# ------------------------------------------------------------------------------------------------ signature
def _flip(hex_sig: str) -> str:
    return ("0" if hex_sig[0] != "0" else "1") + hex_sig[1:]


@pytest.mark.parametrize("bad, status, failed, integrity", [
    (lambda row: _flip(row["signature"]), "INVALID", ["SIGNATURE"], "INTACT"),                    # corrupted
    (lambda row: "zz" * 64, "MALFORMED", ["SIGNATURE_FORMAT"], "INTACT"),                         # not hex
    (lambda row: row["signature"][:-2], "MALFORMED", ["SIGNATURE_FORMAT"], "INTACT"),             # truncated
    # Absent: never assumed valid. A9a's rule-version check also refuses an unsigned release (RELEASE_UNSIGNED).
    (lambda row: "", "MISSING", ["SIGNATURE_PRESENT", "RULE_VERSION_INTEGRITY"], "ALTERED"),
])
def test_a_bad_signature_is_invalid_and_raises_the_security_signal(client, release, bad, status, failed, integrity):
    cr = release["cr"]
    cr_id = cr["content_release_id"]
    events = len(_events(cr_id))
    with _tampered("catalog_content_release", "content_release_id", cr_id, signature=bad):
        rep = _verify(client, cr_id)
    assert (rep["outcome"], rep["integrity"], rep["signature"]["status"]) == ("INVALID", integrity, status)
    assert [c["check"] for c in rep["checks"] if c["status"] == "FAIL"] == failed and rep["signature_ok"] is False
    [ev] = _events(cr_id)[events:]
    assert ev.payload["verification_result"] == failed[0] and ev.payload["failed_checks"] == failed and ev.tenant_id is None
    assert _audits(cr_id)[-1].new_state == "INVALID"
    # Restored by the owner, never re-signed by verification: the original signature verifies again.
    again = _verify(client, cr_id)
    assert again["outcome"] == "UNVERIFIABLE" and _verify(client, cr_id)["signature"]["status"] == "VALID"
    assert client.get("/api/catalog/releases", headers=H(AUTHOR)).json() and _row(cr_id)["signature"] == cr["signature"]


def _row(cr_id) -> dict:
    from app.db import Base, admin_engine
    t = Base.metadata.tables["catalog_content_release"]
    with admin_engine.connect() as c:
        return dict(c.execute(select(t).where(t.c.content_release_id == cr_id)).mappings().one())


@pytest.mark.parametrize("key_id, status, check", [
    ("ed25519:0000000000000000", "KEY_UNAVAILABLE", "SIGNER_KEY_AVAILABLE"),   # unknown key
    ("rsa-pss-sha256:0123456789abcdef", "UNSUPPORTED_ALGORITHM", "SIGNATURE_ALGORITHM"),
    ("", "UNSUPPORTED_ALGORITHM", "SIGNATURE_ALGORITHM"),                       # undeclared algorithm
])
def test_an_unknown_key_or_unsupported_algorithm_is_unverifiable_never_valid(client, release, key_id, status, check):
    cr_id = release["cr"]["content_release_id"]
    with _tampered("catalog_content_release", "content_release_id", cr_id, signer_key_id=key_id):
        rep = _verify(client, cr_id)
    st = _status(rep)
    assert (rep["outcome"], rep["signature"]["status"], st[check]) == ("UNVERIFIABLE", status, "UNVERIFIABLE")
    assert "SIGNATURE" not in st and rep["signature_ok"] is False and not _failed(rep)


def test_without_a_verification_key_nothing_is_minted(client, release, monkeypatch, tmp_path):
    """A cell holding no key reports the release as unverifiable; verification never creates or replaces a key."""
    from app.engines import keys
    absent = tmp_path / "no-signing-key.pem"
    monkeypatch.setattr(keys, "SIGNING_KEY_FILE", absent)
    rep = _verify(client, release["cr"]["content_release_id"])
    assert rep["signature"]["status"] == "KEY_UNAVAILABLE" and rep["public_key_pem"] is None
    assert rep["outcome"] == "UNVERIFIABLE" and not absent.exists()


# ------------------------------------------------------------------------------------------------ payload
def test_an_altered_manifest_is_detected_even_when_its_hash_is_rewritten(client, release):
    from app.platform import canonical_json, sha256_hex
    cr_id = release["cr"]["content_release_id"]
    altered = lambda row: {**row["manifest"], "label": "tampered-label"}
    with _tampered("catalog_content_release", "content_release_id", cr_id, manifest=altered):
        rep = _verify(client, cr_id)
    assert rep["outcome"] == "INVALID" and rep["integrity"] == "ALTERED"
    assert {"MANIFEST_HASH", "SIGNATURE", "MANIFEST_MATCHES_RELEASE_RECORD", "RULE_VERSION_INTEGRITY"} <= _failed(rep)
    # A consistent rewrite of manifest and hash passes every stored-artefact check (A9a); only the signature catches it.
    extra = lambda row: {**row["manifest"], "note": "inserted after signing"}
    with _tampered("catalog_content_release", "content_release_id", cr_id, manifest=extra,
                   manifest_hash=lambda row: sha256_hex(canonical_json(extra(row)))):
        rep = _verify(client, cr_id)
    assert rep["outcome"] == "INVALID" and _failed(rep) == {"SIGNATURE"} and rep["manifest_hash_ok"] is True
    assert _verify(client, cr_id)["outcome"] == "UNVERIFIABLE"


def test_substituted_release_content_is_detected(client, release):
    """Another release's manifest, hash and signature (all genuine) moved onto this release record."""
    cr_id = release["cr"]["content_release_id"]
    other = next(r for r in client.get("/api/catalog/releases", headers=H(AUTHOR)).json() if r["label"].startswith("CR-2026-"))
    with _tampered("catalog_content_release", "content_release_id", cr_id, manifest=other["manifest"],
                   manifest_hash=other["manifest_hash"], signature=other["signature"]):
        rep = _verify(client, cr_id)
    assert rep["outcome"] == "INVALID" and rep["signature"]["status"] == "VALID"  # the signature itself is genuine
    assert {"MANIFEST_MATCHES_RELEASE_RECORD", "RULE_VERSION_BINDS_RELEASE", "RELEASE_COVERS_RULE_VERSIONS"} <= _failed(rep)
    # A rule version re-pointed at another release is inconsistent with the signed manifest.
    rule_id = release["rule"]["rule_version_id"]
    with _tampered("catalog_rule_version", "rule_version_id", rule_id, content_release_id=other["content_release_id"]):
        rep = _verify(client, cr_id)
    assert _failed(rep) == {"RULE_VERSION_BINDS_RELEASE"} and rep["outcome"] == "INVALID"


# ------------------------------------------------------------------------------------------------ bound content
@pytest.mark.parametrize("table, key, ident, changes, expected", [
    ("catalog_requirement_version", "requirement_version_id", "rv", {"locator": "Art. 99"},
     {"REQUIREMENT_VERSION_IDENTITY", "REQUIREMENT_CONTENT_DIGEST"}),
    ("catalog_requirement_version", "requirement_version_id", "rv", {"title": "Silently rewritten title"},
     {"REQUIREMENT_CONTENT_DIGEST"}),
    ("catalog_requirement_version", "requirement_version_id", "rv", {"lifecycle": "REVIEW_REQUIRED"},
     {"REQUIREMENT_VERSION_LIFECYCLE"}),
    ("catalog_provision", "provision_id", "provision", {"normalized_meaning": "A weaker duty than the one released."},
     {"REQUIREMENT_CONTENT_DIGEST"}),
    ("catalog_source_record", "source_id", "source", {"fingerprint": "0" * 64}, {"SOURCE_FINGERPRINT", "SOURCE_CONTENT"}),
    ("catalog_source_record", "source_id", "source", {"raw_text": "Credit institutions may keep a register."},
     {"SOURCE_CONTENT"}),
    ("catalog_source_record", "source_id", "source", {"locator": "OJ L 0/0"}, {"SOURCE_LOCATOR"}),
])
def test_changed_requirement_versions_and_source_fingerprints_are_detected(client, release, table, key, ident, changes,
                                                                           expected):
    ids = {"rv": release["rv"]["requirement_version_id"], "provision": release["rv"]["provisions"][0]["provision_id"],
           "source": release["source"]["source_id"]}
    cr_id = release["cr"]["content_release_id"]
    with _tampered(table, key, ids[ident], **changes):
        rep = _verify(client, cr_id)
    assert (rep["outcome"], rep["integrity"]) == ("INVALID", "ALTERED") and _failed(rep) == expected
    assert rep["signature"]["status"] == "VALID"  # the manifest is untouched: its content no longer matches the records
    assert _verify(client, cr_id)["integrity"] == "INTACT"


@contextmanager
def _removed(table: str, key: str, values: list):
    """Delete rows below the application, then put them back exactly."""
    from app.db import Base, admin_engine
    t = Base.metadata.tables[table]
    with admin_engine.begin() as c:
        rows = [dict(r) for r in c.execute(select(t).where(t.c[key].in_(values))).mappings()]
        c.execute(delete(t).where(t.c[key].in_(values)))
    try:
        yield
    finally:
        with admin_engine.begin() as c:
            for r in rows:
                c.execute(insert(t).values(**r))


def test_missing_referenced_requirement_versions_and_provisions_are_detected(client, release):
    cr_id, rv = release["cr"]["content_release_id"], release["rv"]
    pids = [p["provision_id"] for p in rv["provisions"]]
    with _removed("catalog_provision", "provision_id", pids[:1]):
        rep = _verify(client, cr_id)
    assert rep["outcome"] == "INVALID" and _failed(rep) == {"PROVISIONS_PRESENT"}
    assert "REQUIREMENT_CONTENT_DIGEST" not in _status(rep)  # not computed over an incomplete set
    with _removed("catalog_provision", "provision_id", pids), \
            _removed("catalog_requirement_version", "requirement_version_id", [rv["requirement_version_id"]]):
        rep = _verify(client, cr_id)
    assert rep["outcome"] == "INVALID" and "REQUIREMENT_VERSION_PRESENT" in _failed(rep)
    assert _status(rep)["SOURCE_FINGERPRINT"] == "PASS"  # the source the release bound is still checked
    assert _verify(client, cr_id)["outcome"] == "UNVERIFIABLE"


def test_verification_never_changes_the_release(client, release):
    cr_id = release["cr"]["content_release_id"]
    before = _row(cr_id)
    for _ in range(2):
        _verify(client, cr_id)
    with _tampered("catalog_content_release", "content_release_id", cr_id, signature=lambda row: _flip(row["signature"])):
        assert _verify(client, cr_id)["outcome"] == "INVALID"
    assert _row(cr_id) == before


# ------------------------------------------------------------------------------------------------ outcome rules
@pytest.mark.parametrize("statuses, outcome", [
    (["PASS", "PASS"], "VALID"), (["PASS", "UNVERIFIABLE"], "UNVERIFIABLE"), (["PASS", "FAIL"], "INVALID"),
    (["UNVERIFIABLE", "FAIL"], "INVALID"), ([], "VALID")])
def test_outcome_rules(statuses, outcome):
    """INVALID beats UNVERIFIABLE beats VALID. VALID is reachable only when no check is undecided, which needs a trusted
    content-release key (decision K1); no deployment reaches it today."""
    from app.engines.release_verification import outcome_of
    assert outcome_of([{"status": s} for s in statuses]) == outcome


# ------------------------------------------------------------------------------------------------ access
def test_verification_authorization_and_tenant_isolation(client, release):
    cr_id = release["cr"]["content_release_id"]
    assert client.get(f"/api/catalog/releases/{cr_id}/verify").status_code == 401
    for actor in (SUPER, "actor-agent"):
        n = denials(actor)
        assert client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(actor)).status_code == 403, actor
        assert denials(actor) - n == 1
    assert client.get("/api/catalog/releases/no-such-release/verify", headers=H(AUTHOR)).status_code == 404
    reports = {a: _verify(client, cr_id, a) for a in ("actor-cco", B_CCO, "actor-ext-auditor", AUTHOR)}
    assert len({json.dumps(r, sort_keys=True) for r in reports.values()}) == 1  # global content: one answer for all
    assert "tenant" not in json.dumps(reports[AUTHOR])
    # Each verification is recorded in the verifier's own trail; organisations never see each other's or Zoiko's.
    tenants = {a.actor_id: a.tenant_id for a in _audits(cr_id)}
    assert (tenants["actor-cco"], tenants[B_CCO], tenants[AUTHOR]) == (TENANT, TB, None)
    for actor, own, foreign in (("actor-cco", "actor-cco", B_CCO), (B_CCO, B_CCO, "actor-cco")):
        seen = {e["actor_id"] for e in client.get("/api/audit-events", params={"object_id": cr_id, "limit": 200},
                                                   headers=H(actor)).json()["items"]}
        assert own in seen and foreign not in seen and AUTHOR not in seen
    # An INVALID result found by an organisation user raises the signal in that organisation's own outbox.
    with _tampered("catalog_content_release", "content_release_id", cr_id, signature=lambda row: _flip(row["signature"])):
        assert _verify(client, cr_id, B_CCO)["outcome"] == "INVALID"
    assert _events(cr_id)[-1].tenant_id == TB
