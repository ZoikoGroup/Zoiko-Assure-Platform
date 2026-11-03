"""Rule integrity before binding evaluation (Task 3; audit A9a).

Requirements: APP-REQ-006 and ZA-APP-001 §13 (a binding RuleVersion is checked before input freeze), ZA-RUL-001 §12 and
RUL-REQ-014/016 (the AST hash input, including pinned SetVersion hashes), RUL-REQ-027 (the release manifest binds the
exact RuleVersion and SetVersion hashes), ZA-RUL-001 §18 (`RUL_SIGNATURE_INVALID`), APP-REQ-019 (never UNCERTAIN),
SEC-REQ-048 and ZA-SEC-001 §23 (fail closed and raise `security.content_signature_failed`). ZA-QA-001: "PUBLISHED/
signature/compatibility/effective-date gates" and "semantic change -> changed hash"; no test doubles for integrity.

Published catalogue rows are immutable (migration 0004), so every test tampers below the application: on PostgreSQL the
owner role disables the trigger inside its own transaction (audit A10), which is the attack these checks exist for. Each
tampered row is restored afterwards. Signature verification itself is out of scope here (A9b, blocked on the
content-release trust decision; docs/backend-decision-brief-content-signature-trust.md).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text, update

from conftest import H

CCO = "actor-cco"
TENANT = "tenant-zoiko-group"  # the synthetic Customer Zero tenant the suite seeds
SIGNAL = "security.content_signature_failed"
# Rows a refused evaluation or publication must not leave behind (APP-REQ-019, SEC-REQ-048).
UNCHANGED = ("applicability_evaluation", "applicability_determination_revision", "applicability_fact_request",
             "applicability_fact_conflict", "catalog_rule_version", "catalog_content_release",
             "applicability_fact_assertion_revision")


@contextmanager
def _tampered(table: str, key: str, value, **changes):
    """Rewrite one published catalogue row below the application, then restore it. A change may be a callable that
    derives the new value from the original row."""
    from app.db import IS_POSTGRES, Base, admin_engine
    t = Base.metadata.tables[table]

    def write(conn, values):
        if IS_POSTGRES:  # the owner bypasses the immutability trigger with DDL inside its own transaction (A10)
            conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
        conn.execute(update(t).where(t.c[key] == value).values(**values))
        if IS_POSTGRES:
            conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))

    with admin_engine.begin() as c:
        row = c.execute(select(t).where(t.c[key] == value)).mappings().one()
        original = {k: row[k] for k in changes}
        write(c, {k: v(row) if callable(v) else v for k, v in changes.items()})
    try:
        yield
    finally:
        with admin_engine.begin() as c:
            write(c, original)


def _counts() -> dict:
    from app.db import admin_engine
    with admin_engine.connect() as c:
        out = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in UNCHANGED}
        out["other_events"] = c.execute(text("SELECT count(*) FROM platform_outbox_event WHERE event_type <> :e"),
                                        {"e": SIGNAL}).scalar()
        out["other_audit"] = c.execute(text("SELECT count(*) FROM platform_audit_event WHERE action <> 'content.verify'")).scalar()
        out["signals"] = c.execute(text("SELECT count(*) FROM platform_outbox_event WHERE event_type = :e"),
                                   {"e": SIGNAL}).scalar()
        out["verifications"] = c.execute(text("SELECT count(*) FROM platform_audit_event WHERE action = 'content.verify'")).scalar()
        c.rollback()
    return out


def _assert_refused(resp, before: dict, *, check: str, artifact_id: str | None, rule_version_id: str | None,
                    tenant_id: str | None, actor: str = CCO) -> dict:
    """Refused with RUL_SIGNATURE_INVALID; nothing the operation wrote survives, but the security signal does."""
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "RUL_SIGNATURE_INVALID"
    if rule_version_id:
        assert detail["rule_version_id"] == rule_version_id
    after = _counts()
    assert {k: after[k] - before[k] for k in after} == {**{k: 0 for k in after}, "signals": 1, "verifications": 1}
    with AdminSession() as db:
        ev = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == SIGNAL)
                        .order_by(OutboxEvent.record_seq.desc())).scalars().first()
        au = db.execute(select(AuditEvent).where(AuditEvent.action == "content.verify")
                        .order_by(AuditEvent.record_seq.desc())).scalars().first()
        artifact = artifact_id or detail["rule_version_id"]
        assert ev.payload == {"content_release_id": ev.aggregate_id, "rule_version_id": detail["rule_version_id"],
                              "artifact_id": artifact, "cell_id": "eu-west-cell-a", "verification_result": check}
        assert (ev.tenant_id, au.tenant_id, au.actor_id, au.object_id, au.new_state, au.reason, au.record_seq) == (
            tenant_id, tenant_id, actor, detail["rule_version_id"], "RUL_SIGNATURE_INVALID", f"{check}: {artifact}",
            ev.record_seq)
    return detail


def _bind(client, *, with_sets: bool = False) -> dict:
    """Evaluate a seeded (subject, obligation) pair now, untampered, and return the RuleVersion it bound."""
    from app.db import AdminSession
    from app.models import RuleVersion
    seen = set()
    for row in client.get("/api/applicability/evaluations", headers=H(CCO)).json():
        pair = (row["subject_id"], row["obligation_definition_id"])
        if pair in seen:
            continue
        seen.add(pair)
        r = client.post("/api/applicability/evaluations", headers=H(CCO),
                        json={"subject_id": pair[0], "obligation_definition_id": pair[1]})
        if r.status_code == 409:  # no RuleVersion binds today (e.g. RULE_NOT_EFFECTIVE): not a candidate
            continue
        assert r.status_code == 200, r.text
        ev = r.json()
        with AdminSession() as db:
            rv = db.get(RuleVersion, ev["rule_version_id"])
            if with_sets and not rv.set_refs:
                continue
            return {"subject_id": pair[0], "obligation_definition_id": pair[1], "evaluation_id": ev["evaluation_id"],
                    "record_cutoff_seq": ev["record_cutoff_seq"], "rule_version_id": rv.rule_version_id,
                    "content_release_id": rv.content_release_id,
                    "set_version_id": rv.set_refs[0]["set_version_id"] if rv.set_refs else None}
    pytest.fail("the seeded fixture has no evaluable subject/obligation pair" + (" with a SetVersion" if with_sets else ""))


def _evaluate(client, b: dict):
    return client.post("/api/applicability/evaluations", headers=H(CCO),
                       json={"subject_id": b["subject_id"], "obligation_definition_id": b["obligation_definition_id"]})


def _verified_republication(client, rule_version_id: str) -> tuple[dict, str]:
    """A republication draft of a current RuleVersion, through every gate before release (author, peer, legal, shadow)."""
    base = next(v for v in client.get("/api/rules/versions", headers=H("actor-content-analyst")).json()
                if v["rule_version_id"] == rule_version_id)
    draft = client.post("/api/rules/drafts", headers=H("actor-content-analyst"), json={
        "obligation_definition_id": base["obligation_definition_id"], "authoring_mode": "AST",
        "ast": base["canonical_ast"], "golden_vectors": base["golden_vectors"], "source_mappings": base["source_mappings"],
        "valid_from": base["valid_from"], "change_class": "EXECUTABLE_OUTCOME"})
    assert draft.status_code == 200, draft.text
    did = draft.json()["draft_id"]
    for actor, step in (("actor-content-analyst", "submit-review"), ("actor-encoder", "peer-approve"),
                        ("actor-content-legal", "legal-approve"), ("actor-release", "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return base, did


def _altered_ast(row):
    return {**row["canonical_ast"], "za_tampered": True}


# ---------------------------------------------------------------- untampered content still verifies and evaluates
def test_ast_hash_binds_the_declared_versions():
    """The hash input is the declared schema and operator-set versions plus the canonical AST and set hashes (ZA-RUL-001
    §12); the defaults are this build's versions, so publication hashes are unchanged (RUL-REQ-016)."""
    from app.engines import rules
    ast, sets = {"node_type": "CONST", "type": "BOOLEAN", "value": True}, {"set_x@1": "ab" * 32}
    h = rules.ast_hash(ast, sets)
    assert h == rules.ast_hash(ast, sets, rule_schema_version=rules.SCHEMA_VERSION,
                               operator_set_version=rules.OPERATOR_SET_VERSION)
    assert h != rules.ast_hash(ast, sets, rule_schema_version="9.9")
    assert h != rules.ast_hash(ast, sets, operator_set_version="9.9")
    assert h != rules.ast_hash(ast, {"set_x@1": "cd" * 32})


def test_every_published_rule_version_verifies(client):
    """Regression: every RuleVersion the seed and the publication path produced passes the checks, so legitimate
    evaluations are unaffected."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import RuleVersion
    _bind(client)
    with AdminSession() as db:
        versions = db.execute(select(RuleVersion).where(RuleVersion.status == "PUBLISHED")).scalars().all()
        assert versions
        for rv in versions:
            appl.verify_rule_integrity(db, rv)


# ---------------------------------------------------------------- 3.1 authoritative AST hash, with its pinned sets
def test_altered_ast_is_refused(client):
    b = _bind(client)
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], canonical_ast=_altered_ast):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="AST_HASH_MISMATCH", artifact_id=b["rule_version_id"],
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)
    assert _evaluate(client, b).status_code == 200  # restored content evaluates again


def test_declared_schema_version_is_part_of_the_hash(client):
    b = _bind(client)
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], rule_schema_version="9.9"):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="AST_HASH_MISMATCH", artifact_id=b["rule_version_id"],
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)


def test_altered_set_members_are_refused(client):
    """Set members are read at evaluation, so a SetVersion is re-hashed from its members (RUL-REQ-014)."""
    b = _bind(client, with_sets=True)
    with _tampered("catalog_controlled_set_version", "set_version_id", b["set_version_id"],
                   members=lambda row: [*row["members"], "ZA-TAMPERED"]):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="SET_HASH_MISMATCH", artifact_id=b["set_version_id"],
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)


# ---------------------------------------------------------------- 3.2 manifest hash
def test_altered_manifest_is_refused(client):
    b = _bind(client)
    with _tampered("catalog_content_release", "content_release_id", b["content_release_id"],
                   manifest=lambda row: {**row["manifest"], "label": "za-tampered"}):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="MANIFEST_HASH_MISMATCH", artifact_id=b["content_release_id"],
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)


# ---------------------------------------------------------------- 3.3 the release covers this exact RuleVersion
def test_consistent_rewrite_of_the_rule_row_is_refused(client):
    """An AST rewritten together with its stored hash passes the hash check; the release manifest still binds the
    original hash (RUL-REQ-027)."""
    from app.engines import rules
    b = _bind(client)

    def rehashed(row):
        pins = {r["set_version_id"]: r["sha256"] for r in row["set_refs"] or []}
        return rules.ast_hash(_altered_ast(row), pins, rule_schema_version=row["rule_schema_version"],
                              operator_set_version=row["operator_set_version"])
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], canonical_ast=_altered_ast,
                   ast_hash=rehashed):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="RELEASE_DOES_NOT_COVER_RULE_VERSION",
                        artifact_id=b["content_release_id"], rule_version_id=b["rule_version_id"], tenant_id=TENANT)


def test_release_of_another_rule_version_is_refused(client):
    from app.db import AdminSession
    from app.models import ContentRelease
    b = _bind(client)
    with AdminSession() as db:
        other = db.execute(select(ContentRelease.content_release_id).where(
            ContentRelease.content_release_id != b["content_release_id"])).scalars().first()
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], content_release_id=other):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="RELEASE_DOES_NOT_COVER_RULE_VERSION", artifact_id=other,
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)


def test_missing_release_is_refused(client):
    b = _bind(client)
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], content_release_id="za-missing-release"):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="RELEASE_NOT_PUBLISHED", artifact_id="za-missing-release",
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)


def test_effective_period_differing_from_the_release_is_refused(client):
    """The release declares the RuleVersion's effective period; a row moved to another period is not the released
    version (APP-REQ-006: effective for the evaluation time)."""
    b = _bind(client)
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"],
                   valid_from=lambda row: row["valid_from"] - timedelta(days=1)):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="RELEASE_ENTRY_MISMATCH", artifact_id=b["content_release_id"],
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)


# ---------------------------------------------------------------- 3.4 no fallback, every evaluation path fails closed
def test_tampered_newest_version_is_refused_without_falling_back(client):
    """When the newest effective RuleVersion fails, evaluation is refused; it never binds the previous version."""
    b = _bind(client)
    base, did = _verified_republication(client, b["rule_version_id"])
    pub = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                      json={"label": "rule-integrity-fallback-probe", "changelog": "Republication of an unchanged AST"})
    assert pub.status_code == 200, pub.text
    newest = pub.json()["rule_version"]
    assert newest["version"] == base["version"] + 1
    assert _evaluate(client, b).json()["rule_version_id"] == newest["rule_version_id"]
    with _tampered("catalog_rule_version", "rule_version_id", newest["rule_version_id"], canonical_ast=_altered_ast):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="AST_HASH_MISMATCH", artifact_id=newest["rule_version_id"],
                        rule_version_id=newest["rule_version_id"], tenant_id=TENANT)


def test_every_evaluation_path_fails_closed(client):
    """Batch evaluation, the reproducibility replay and historical comparison refuse too, with no partial result."""
    b = _bind(client)
    paths = {
        "evaluate-all": lambda: client.post("/api/applicability/evaluate-all", headers=H(CCO),
                                            params={"subject_id": b["subject_id"]}),
        "replay": lambda: client.get(f"/api/applicability/evaluations/{b['evaluation_id']}", headers=H(CCO)),
        "history-compare": lambda: client.get("/api/history/compare", headers=H(CCO), params={
            "subject_id": b["subject_id"], "obligation_definition_id": b["obligation_definition_id"],
            "valid_at": datetime.now(timezone.utc).isoformat(), "left_seq": b["record_cutoff_seq"],
            "right_seq": b["record_cutoff_seq"]}),
    }
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], canonical_ast=_altered_ast):
        for name, call in paths.items():
            before = _counts()
            _assert_refused(call(), before, check="AST_HASH_MISMATCH", artifact_id=b["rule_version_id"],
                            rule_version_id=b["rule_version_id"], tenant_id=TENANT)
    assert client.get(f"/api/applicability/evaluations/{b['evaluation_id']}", headers=H(CCO)).json()["replay"]["matches"]


def test_publication_fails_closed_when_impact_reevaluation_meets_tampered_content(client):
    """A tampered SetVersion met during rule-release impact re-evaluation fails the whole publication (SEC-REQ-048: the
    affected release operation), instead of being skipped like an ordinary evaluation error. The signal concerns
    cell-wide content, so it carries no tenant."""
    from app.db import admin_engine
    b = _bind(client, with_sets=True)
    _, did = _verified_republication(client, b["rule_version_id"])
    with _tampered("catalog_controlled_set_version", "set_version_id", b["set_version_id"],
                   members=lambda row: [*row["members"], "ZA-TAMPERED"]):
        before = _counts()
        resp = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                           json={"label": "rule-integrity-impact-probe", "changelog": "Publication meets a tampered set"})
        detail = _assert_refused(resp, before, check="SET_HASH_MISMATCH", artifact_id=b["set_version_id"],
                                 rule_version_id=None, tenant_id=None, actor="actor-release")
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM catalog_rule_version WHERE draft_id = :d OR rule_version_id = :r"),
                         {"d": did, "r": detail["rule_version_id"]}).scalar() == 0
        assert c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).scalar() == "VERIFIED"
        c.rollback()


# ---------------------------------------------------------------- fact assertion triggers fail-closed on tampered content
def test_fact_assertion_fails_closed_when_rule_tampered(client):
    """When a fact assertion triggers re-evaluation against a tampered RuleVersion,
    the operation fails closed: the fact assertion and partial evaluations are rolled back,
    security.content_signature_failed and content.verify are recorded, and HTTP 500
    RUL_SIGNATURE_INVALID is returned."""
    from app.db import AdminSession
    from app.models import FactDefinition, RuleVersion
    b = _bind(client)
    with AdminSession() as db:
        rv = db.get(RuleVersion, b["rule_version_id"])
        req_fact = rv.required_facts[0]
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == req_fact)).scalars().first()
        val = True if fd.data_type == "BOOLEAN" else (100 if fd.data_type == "INTEGER" else (fd.enum_domain[0] if fd.enum_domain else "GB"))
    with _tampered("catalog_rule_version", "rule_version_id", b["rule_version_id"], canonical_ast=_altered_ast):
        before = _counts()
        resp = client.post(f"/api/subjects/{b['subject_id']}/facts", headers=H(CCO), json={
            "fact_key": fd.key, "value": val, "valid_from": "2025-01-01T00:00:00Z",
            "change_kind": "CORRECTION", "reason": "Fact assertion on tampered rule probe"})
        _assert_refused(resp, before, check="AST_HASH_MISMATCH", artifact_id=b["rule_version_id"],
                        rule_version_id=b["rule_version_id"], tenant_id=TENANT)
    # Untampered: ordinary successful fact assertion works
    ok_resp = client.post(f"/api/subjects/{b['subject_id']}/facts", headers=H(CCO), json={
        "fact_key": fd.key, "value": val, "valid_from": "2025-01-01T00:00:00Z",
        "change_kind": "CORRECTION", "reason": "Restored fact assertion"})
    assert ok_resp.status_code == 200, ok_resp.text
    assert "revisions" in ok_resp.json()


def test_fact_assertion_fails_closed_when_set_tampered(client):
    """When a fact assertion triggers re-evaluation against a tampered SetVersion,
    the operation fails closed with SET_HASH_MISMATCH."""
    from app.db import AdminSession
    from app.models import FactDefinition, RuleVersion
    b = _bind(client, with_sets=True)
    with AdminSession() as db:
        rv = db.get(RuleVersion, b["rule_version_id"])
        req_fact = rv.required_facts[0]
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == req_fact)).scalars().first()
        val = True if fd.data_type == "BOOLEAN" else (100 if fd.data_type == "INTEGER" else (fd.enum_domain[0] if fd.enum_domain else "GB"))
    with _tampered("catalog_controlled_set_version", "set_version_id", b["set_version_id"],
                   members=lambda row: [*row["members"], "ZA-TAMPERED"]):
        before = _counts()
        resp = client.post(f"/api/subjects/{b['subject_id']}/facts", headers=H(CCO), json={
            "fact_key": fd.key, "value": val, "valid_from": "2025-01-01T00:00:00Z",
            "change_kind": "CORRECTION", "reason": "Fact assertion on tampered set probe"})
        _assert_refused(resp, before, check="SET_HASH_MISMATCH", artifact_id=b["set_version_id"],
                        rule_version_id=None, tenant_id=TENANT)


def test_fact_assertion_preserves_non_integrity_evaluation_errors(client):
    """Non-integrity evaluation errors during fact-triggered re-evaluation (such as
    COVERAGE_NOT_ENCODED or RULE_NOT_EFFECTIVE) are captured in impact_reevaluations without failing closed or
    aborting the fact assertion.

    The error is provoked with catalogue state no release signature covers: the obligation definition is switched to
    MONITORING_ONLY coverage. (Moving a published RuleVersion's valid_to below the application, as this test used to,
    is tampering with signed content and now fails closed: test_altered_validity_of_the_newest_version_fails_closed.)"""
    from app.db import AdminSession
    from app.models import FactDefinition, RuleVersion
    b = _bind(client)
    with AdminSession() as db:
        rv = db.get(RuleVersion, b["rule_version_id"])
        req_fact = rv.required_facts[0]
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == req_fact)).scalars().first()
        val = True if fd.data_type == "BOOLEAN" else (100 if fd.data_type == "INTEGER" else (fd.enum_domain[0] if fd.enum_domain else "GB"))
    # Evaluation of this obligation now raises HTTPException 409 COVERAGE_NOT_ENCODED (not an integrity failure)
    with _tampered("catalog_obligation_definition", "obligation_definition_id", b["obligation_definition_id"],
                   coverage_mode="MONITORING_ONLY"):
        resp = client.post(f"/api/subjects/{b['subject_id']}/facts", headers=H(CCO), json={
            "fact_key": fd.key, "value": val, "valid_from": "2025-01-01T00:00:00Z",
            "change_kind": "CORRECTION", "reason": "Non-integrity evaluation error probe"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["revisions"]) > 0
        assert any("error" in reeval for reeval in data["impact_reevaluations"])
        assert any(isinstance(r.get("error"), dict) and r["error"].get("error") == "COVERAGE_NOT_ENCODED"
                   for r in data["impact_reevaluations"])


def test_altered_validity_of_the_newest_version_fails_closed(client):
    """Task 0: a newer PUBLISHED version whose row period was moved so that it no longer covers the evaluation time is
    not silently passed over for the previous version; its period no longer matches its signed release (RUL-REQ-027)."""
    b = _bind(client)
    base, did = _verified_republication(client, b["rule_version_id"])
    pub = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                      json={"label": "rule-integrity-validity-probe", "changelog": "Republication of an unchanged AST"})
    assert pub.status_code == 200, pub.text
    newest = pub.json()["rule_version"]
    assert _evaluate(client, b).json()["rule_version_id"] == newest["rule_version_id"]
    with _tampered("catalog_rule_version", "rule_version_id", newest["rule_version_id"],
                   valid_to=datetime(2020, 1, 1).date()):
        before = _counts()
        _assert_refused(_evaluate(client, b), before, check="RELEASE_ENTRY_MISMATCH",
                        artifact_id=newest["content_release_id"], rule_version_id=newest["rule_version_id"],
                        tenant_id=TENANT)


# ---------------------------------------------------------------- publication-time SetVersion verification
def test_publication_refuses_tampered_set_when_zero_subjects_evaluated(client):
    """Publication-time SetVersion verification runs and fails closed even when zero subjects
    have previously been evaluated for the obligation definition."""
    from app.db import AdminSession, admin_engine
    from app.models import ObligationDefinition
    b = _bind(client, with_sets=True)
    import secrets
    key_suffix = secrets.token_hex(4).upper()
    with AdminSession() as db:
        orig_od = db.get(ObligationDefinition, b["obligation_definition_id"])
        new_od = ObligationDefinition(
            key=f"ZERO_SUBJ_TEST_OBLIGATION_{key_suffix}",
            title="Zero Subjects Test Obligation",
            instrument_id=orig_od.instrument_id,
            subject_type=orig_od.subject_type,
            coverage_mode="ENCODED",
            provision_ids=orig_od.provision_ids,
            material=True,
        )
        db.add(new_od)
        db.commit()
        new_od_id = new_od.obligation_definition_id

    base = next(v for v in client.get("/api/rules/versions", headers=H("actor-content-analyst")).json()
                if v["rule_version_id"] == b["rule_version_id"])
    draft = client.post("/api/rules/drafts", headers=H("actor-content-analyst"), json={
        "obligation_definition_id": new_od_id, "authoring_mode": "AST",
        "ast": base["canonical_ast"], "golden_vectors": base["golden_vectors"], "source_mappings": base["source_mappings"],
        "valid_from": base["valid_from"], "change_class": "CORRECTION"})  # DraftIn has no reason field (API-REQ-014: unknown fields refused)
    assert draft.status_code == 200, draft.text
    did = draft.json()["draft_id"]
    for actor, step in (("actor-content-analyst", "submit-review"), ("actor-encoder", "peer-approve"),
                        ("actor-content-legal", "legal-approve"), ("actor-release", "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)

    # Tamper with the referenced SetVersion: publication must fail closed even with 0 subjects
    with _tampered("catalog_controlled_set_version", "set_version_id", b["set_version_id"],
                   members=lambda row: [*row["members"], "ZA-TAMPERED"]):
        before = _counts()
        resp = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                           json={"label": "zero-subjects-tampered-set-probe", "changelog": "Zero subjects probe"})
        detail = _assert_refused(resp, before, check="SET_HASH_MISMATCH", artifact_id=b["set_version_id"],
                                 rule_version_id=None, tenant_id=None, actor="actor-release")
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM catalog_rule_version WHERE draft_id = :d OR rule_version_id = :r"),
                         {"d": did, "r": detail["rule_version_id"]}).scalar() == 0
        assert c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).scalar() == "VERIFIED"
        c.rollback()

    # Now verify untampered publication succeeds with 0 subjects
    pub_ok = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                         json={"label": "zero-subjects-valid-publish", "changelog": "Valid publication zero subjects"})
    assert pub_ok.status_code == 200, pub_ok.text
    assert pub_ok.json()["rule_version"]["obligation_definition_id"] == new_od_id
    assert pub_ok.json()["rule_version"]["status"] == "PUBLISHED"


def test_publication_refuses_missing_set_version(client):
    """Publication of a rule referencing a non-existent SetVersion fails with HTTP 409 SET_VERSION_MISSING."""
    import copy
    from app.db import admin_engine
    b = _bind(client, with_sets=True)
    base = next(v for v in client.get("/api/rules/versions", headers=H("actor-content-analyst")).json()
                if v["rule_version_id"] == b["rule_version_id"])
    tampered_ast = copy.deepcopy(base["canonical_ast"])

    def replace_set(node):
        if isinstance(node, dict):
            if node.get("node_type") == "SET_REF":
                node["set_version_id"] = "set-version-does-not-exist"
            for v in node.values():
                replace_set(v)
        elif isinstance(node, list):
            for item in node:
                replace_set(item)

    replace_set(tampered_ast)
    draft = client.post("/api/rules/drafts", headers=H("actor-content-analyst"), json={
        "obligation_definition_id": base["obligation_definition_id"], "authoring_mode": "AST",
        "ast": tampered_ast, "golden_vectors": base["golden_vectors"], "source_mappings": base["source_mappings"],
        "valid_from": base["valid_from"], "change_class": "CORRECTION"})  # DraftIn has no reason field (API-REQ-014: unknown fields refused)
    assert draft.status_code == 200, draft.text
    did = draft.json()["draft_id"]

    # Advance status to VERIFIED directly to test publication gate on missing SetVersion
    with admin_engine.begin() as c:
        c.execute(text("UPDATE catalog_rule_draft SET status = 'VERIFIED' WHERE draft_id = :d"), {"d": did})

    resp = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                       json={"label": "missing-set-probe", "changelog": "Probe missing set"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "SET_VERSION_MISSING"
