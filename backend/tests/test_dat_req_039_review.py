"""DAT-REQ-039 engineering review of migrations 0011-0013: regression tests for the confirmed findings and the combined
cross-migration privilege boundary (docs/dat-req-039-review-report.md, docs/dat-req-039-remediation-report.md).

Findings under regression test (specification text):
- R039-01 IAM-REQ-036 ("current role/grant/revocation state MUST still be evaluated at commit time"), IAM-REQ-015: a
  policy decision and a concurrent revocation of the grant it relies on must not interleave so that a PERMIT commits
  after the revocation.
- R039-02 ZA-DOM-001 §10.2 ("A0-A4 is vocabulary for the authority level on a grant"): the database accepts only that
  vocabulary; an out-of-vocabulary row would otherwise break every decision for that agent and action.
- R039-03 DAT-REQ-027 / ZA-DAT-001 §13 (least privilege): no runtime path creates connector registry or contract-version
  rows, so the runtime role cannot insert them, not even as a platform principal (which would otherwise create a
  CERTIFIED connector or contract version with no matrix check and no history).
- R039-04 API-REQ-033 ("API input parsers MUST enforce ... field length ... limits"): grant fields longer than their
  columns are refused with 422, not a database error.
- R039-05 EVT-REQ-008 (registered event types): the reviewed paths emit only catalogued names, and no new uncatalogued
  name may appear anywhere (ratchet over the known pre-existing list).

The PostgreSQL tests talk to the database directly as the runtime role, below every application check. They do not
claim anything about PostgreSQL superusers, the table owner's DDL (A10) or BYPASSRLS roles: the database cannot
constrain those.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from conftest import H, stamp

APP = Path(__file__).resolve().parent.parent / "app"
TENANT = "tenant-zoiko-group"
LEAD = "actor-aigov"
AGENT_SUBJECT = "subj-ai-payables"

# ZA-EVT-001 catalogue names this review covers (§19 catalogue; checked against the specification text)
REVIEWED_CATALOGUED = {"authority_grant.issued", "authority_grant.revoked", "connector.certification_changed"}
# Emitted names absent from the whole of ZA-EVT-001 on 3 November 2026 (pre-existing; outside 0011-0013; EVT owner to map).
KNOWN_UNCATALOGUED = {
    "ai_asset.reclassification_required", "ai_asset.registered", "assurance.issue_created", "assurance.issue_transitioned",
    "compensation.submitted", "evidence.conflict_opened", "evidence.conflict_resolved", "exception.requested",
    "ipe.evaluated", "ipe.failed", "obligation.adopted", "obligation.occurrence_fulfilled",
    "obligation.occurrence_scheduled", "regulatory.extraction_proposed", "regulatory.source_ingested",
    "subject.registered", "test.run_recorded"}
# Emitted names that appear in ZA-EVT-001 (generated from the code and the specification text, 3 November 2026)
KNOWN_CATALOGUED = {
    "applicability.changed", "applicability.evaluated", "applicability.uncertain_raised", "assurance.proof_gap_detected",
    "assurance.snapshot_created", "assurance.state_changed", "authority_grant.issued", "authority_grant.revoked",
    "connector.certification_changed", "content_release.corrected", "content_release.published", "control.evaluated",
    "determination.adopted", "determination.challenged", "determination.proposed", "entity.registered",
    "evidence.captured", "evidence.chain_checkpointed", "evidence.erased", "evidence.hold_placed",
    "evidence.integrity_failed", "evidence.invalidated", "evidence.package_signed", "evidence.superseded",
    "evidence.verified", "fact.changed", "fact.requested", "privacy.legal_hold_released",
    # ZA-EVT-001 §19 catalog events, checked against the specification text on 4 November 2026 (published at release;
    # superseded when a newer version of the same requirement is released).
    "requirement.version_published", "requirement.version_superseded", "rule.draft_created",
    "rule.review_requested", "rule.shadow_completed", "rule.validation_failed", "rule.verified",
    # ZA-EVT-001 assurance catalogue: "issue.closed  Issue workflow closed; no direct assurance mutation implied"
    # (checked against the specification text on 5 November 2026; emitted on verified closure).
    "issue.closed",
    # ZA-EVT-001: "issue.created  Issue/remediation case created from control/assurance condition" (checked against the
    # specification text on 5 November 2026; emitted when a control evaluation raises a control-level issue).
    "issue.created",
    # ZA-EVT-001: "determination.rejected  Authorized customer rejected proposed determination" and "obligation.revised
    # Bitemporal obligation revision recorded" (checked against the specification text on 6 November 2026; emitted on a
    # REJECT attestation and on the CLOSED obligation revision an adopted non-APPLIES determination records).
    "determination.rejected", "obligation.revised",
    # ZA-EVT-001 "rule.superseded: Prior RuleVersion no longer current for new binding evaluations" (checked against the
    # specification text on 6 November 2026; emitted when a newer RuleVersion in force is published).
    "rule.superseded",
    "security.content_signature_failed", "temporal.retroactive_change_detected"}
assert REVIEWED_CATALOGUED <= KNOWN_CATALOGUED and not KNOWN_CATALOGUED & KNOWN_UNCATALOGUED


def _emitted(path: Path) -> set[str]:
    return set(re.findall(r'event_type="([a-z_]+\.[a-z_]+)"', path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------------------------------------ both databases
def test_r039_05_reviewed_paths_emit_only_catalogued_events():
    gov = (APP / "engines" / "governance.py").read_text(encoding="utf-8")
    grant_section = gov[gov.index("# 7. Agent authority grants"):]
    assert set(re.findall(r'event_type="([a-z_]+\.[a-z_]+)"', grant_section)) == {"authority_grant.issued",
                                                                                "authority_grant.revoked"}
    assert _emitted(APP / "engines" / "connectors.py") == {"connector.certification_changed"}
    assert "agent.grant_issued" not in (APP / "routers" / "ai.py").read_text(encoding="utf-8")


def test_r039_05_no_new_uncatalogued_event_name_appears():
    """A ratchet: every emitted name is either known-catalogued or on the known pre-existing violation list. A new name
    fails here until it is checked against ZA-EVT-001 (EVT-REQ-008) and classified."""
    emitted = set().union(*(_emitted(p) for p in APP.rglob("*.py")))
    unclassified = emitted - KNOWN_CATALOGUED - KNOWN_UNCATALOGUED
    assert unclassified == set(), f"classify against ZA-EVT-001 first: {sorted(unclassified)}"


@pytest.mark.parametrize("field, size", [("action_class", 120), ("scope", 200), ("reversibility", 64), ("fail_mode", 16),
                                         ("agent_subject_id", 36), ("authority_level", 8)])
def test_r039_04_oversized_grant_fields_are_refused_with_422(client, field, size):
    from test_authorization import db_fingerprint
    body = {"agent_subject_id": AGENT_SUBJECT, "action_class": "PREPARE_FILING_DRAFT", "authority_level": "A2",
            "scope": "r039 scope", "reversibility": "Reversible", "expires_in_days": 30, field: "x" * (size + 1)}
    before = db_fingerprint()
    r = client.post("/api/ai/grants", headers=H(LEAD), json=body)
    assert r.status_code == 422, (field, r.status_code, r.text[:200])
    assert db_fingerprint() == before


def test_r039_agent_and_workload_principals_cannot_reach_any_reviewed_write(client):
    """API layer, all three migrations: an AI agent and a workload are refused before any write."""
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    for actor in ("actor-agent", "actor-svc"):
        assert client.post("/api/ai/grants", headers=H(actor), json={
            "agent_subject_id": AGENT_SUBJECT, "action_class": "X", "authority_level": "A0", "scope": "s",
            "reversibility": "r"}).status_code == 403
        assert client.post("/api/ai/grants/any/revoke", headers=H(actor)).status_code == 403
        assert client.post("/api/connectors/any/certification", headers=H(actor),
                           json={"certification": "SUSPENDED", "reason": "x"}).status_code == 403
        assert client.post("/api/connectors/any/contracts/mapping/any/certification", headers=H(actor),
                           json={"certification": "SUSPENDED", "reason": "x"}).status_code == 403
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ PostgreSQL below the API
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES, admin_engine, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine, engine


def _try(conn, sql, params=None):
    """Run one statement under a savepoint; return 'ok:<rowcount>' or the error class (privilege / rls / check /
    trigger / fk / other)."""
    sp = conn.begin_nested()
    try:
        n = conn.execute(text(sql), params or {}).rowcount
        sp.rollback()
        return f"ok:{n}"
    except DBAPIError as e:
        sp.rollback()
        msg = str(e.orig)
        for key, label in (("permission denied", "privilege"), ("row-level security", "rls"), ("check constraint", "check"),
                           ("append-only", "trigger"), ("is final once", "trigger"), ("foreign key", "fk"),
                           ("duplicate key", "unique")):
            if key in msg:
                return label
        return "other:" + msg.splitlines()[0][:80]


def test_r039_02_out_of_vocabulary_authority_level_is_refused_by_the_database(client, pg):
    _, runtime = pg
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, TENANT, actor=LEAD)
        sql = ("INSERT INTO ai_authority_grant (grant_id, tenant_id, agent_subject_id, action_class, authority_level, scope, "
               "reversibility, expires_at, granted_by, status, fail_mode, created_at) VALUES (left(md5(random()::text), 32), "
               ":t, :a, 'R039_PROBE', :lvl, 'r039', 'r', now() + interval '1 day', :g, 'ACTIVE', 'FAIL_CLOSED', now())")
        for lvl in ("A0", "A1", "A2", "A3", "A4"):
            assert _try(c, sql, {"t": TENANT, "a": AGENT_SUBJECT, "lvl": lvl, "g": LEAD}) == "ok:1", lvl
        for lvl in ("A5", "A9", "a2", "", "ROOT"):
            assert _try(c, sql, {"t": TENANT, "a": AGENT_SUBJECT, "lvl": lvl, "g": LEAD}) == "check", lvl
        tx.rollback()


def test_r039_03_runtime_role_cannot_create_registry_or_contract_rows(pg):
    _, runtime = pg
    inserts = {
        "integration_connector_definition":
            "INSERT INTO integration_connector_definition (connector_id, key, name, provider, source_class, build_version, "
            "release_availability, certification, delivery_model, runtime_health, supported_objects, certification_matrix, "
            "known_limitations) VALUES (left(md5(random()::text), 32), 'r039-' || md5(random()::text), 'n', 'p', 'IDENTITY', "
            "'1', 'PLANNED', 'CERTIFIED', 'STANDARD', 'HEALTHY', '[]', '{}', '[]')",
        "integration_mapping_contract_version":
            "INSERT INTO integration_mapping_contract_version (mapping_contract_version_id, connector_id, version, "
            "source_schema_fingerprint, source_fields, field_mappings, relied_upon_fields, status) SELECT "
            "left(md5(random()::text), 32), connector_id, 900 + floor(random() * 99)::int, 'fp', '[]', '[]', '[]', "
            "'CERTIFIED' FROM integration_connector_definition LIMIT 1",
        "integration_extraction_contract_version":
            "INSERT INTO integration_extraction_contract_version (extraction_contract_version_id, connector_id, version, "
            "source_object, endpoint_id, query_template, record_identity, required_scopes, ordering, consistency_strategy, "
            "pagination_method, population_proof_methods, known_limitations, status) SELECT left(md5(random()::text), 32), "
            "connector_id, 900 + floor(random() * 99)::int, 'o', 'e', 'q', 'id', '[]', 'id', 'SNAPSHOT_TOKEN', 'c', '[]', "
            "'[]', 'CERTIFIED' FROM integration_connector_definition LIMIT 1"}
    for ctx in ({"tenant": None, "actor": "vc-certifier"}, {"tenant": TENANT, "actor": "actor-cco"}):
        with runtime.connect() as c:
            tx = c.begin()
            stamp(c, ctx["tenant"], actor=ctx["actor"])
            for table, sql in inserts.items():
                assert _try(c, sql) == "privilege", (table, ctx)
            tx.rollback()


def test_r039_01_a_decision_and_a_concurrent_revocation_are_serialised(client, pg):
    """The decision holds its grant under a share lock until it commits, so a revocation that starts meanwhile waits:
    no PERMIT can commit after the revocation of the grant it cites (IAM-REQ-036)."""
    from app.db import SessionLocal, set_principal
    from app.engines import governance as gov
    from app.models import Actor, AuthorityGrantEvent, PolicyDecisionReceipt
    from app.routers.ai import DecisionReq, _decide
    from app.security import Ctx
    scope = "r039 race " + secrets.token_hex(4)
    g = client.post("/api/ai/grants", headers=H(LEAD), json={
        "agent_subject_id": AGENT_SUBJECT, "action_class": "R039_RACE", "authority_level": "A2", "scope": scope,
        "reversibility": "r", "expires_in_days": 1}).json()
    revoked_at, errors = {}, []
    with SessionLocal() as db:
        set_principal(db, TENANT, LEAD)
        ctx = Ctx(db=db, actor=db.get(Actor, LEAD))
        receipt = _decide(ctx, DecisionReq(agent_subject_id=AGENT_SUBJECT, action_class="R039_RACE", scope=scope))
        assert receipt.decision == "PERMIT"

        def revoke():
            try:
                with SessionLocal() as db2:
                    set_principal(db2, TENANT, LEAD)
                    gov.revoke_authority_grant(db2, tenant_id=TENANT, grant_id=g["grant_id"], actor=LEAD, reason="race")
                    db2.commit()
                    revoked_at["t"] = time.monotonic()
            except Exception as e:  # noqa: BLE001 - asserted below
                errors.append(e)
        t = threading.Thread(target=revoke)
        t.start()
        t.join(2.0)
        assert t.is_alive(), "the revocation committed while a PERMIT citing the grant was still uncommitted"
        db.commit()
        committed_at = time.monotonic()
        receipt_id = receipt.receipt_id
    t.join(30)
    assert errors == [] and revoked_at["t"] >= committed_at
    from app.db import AdminSession
    with AdminSession() as db:
        assert db.get(PolicyDecisionReceipt, receipt_id).decision == "PERMIT"
        assert [e.event_type for e in db.execute(select(AuthorityGrantEvent).where(
            AuthorityGrantEvent.grant_id == g["grant_id"])).scalars()] == ["ISSUED", "REVOKED"]
    d = client.post("/api/ai/decisions", headers=H(LEAD), json={
        "agent_subject_id": AGENT_SUBJECT, "action_class": "R039_RACE", "scope": scope}).json()
    assert d["decision"] == "DENY"


# Cross-migration boundary: (context, table, statement) -> outcome. Contexts are signed exactly as the API signs them.
CONTEXTS = {
    "tenant_user": dict(tenant=TENANT, actor="actor-cco"),
    "ai_agent": dict(tenant=TENANT, actor="actor-agent"),       # the database sees an agent as a tenant principal
    "platform_user": dict(tenant=None, actor="vc-certifier"),
    "system_job": dict(tenant=None, actor=None, flags=("system",)),
    "no_context": None,
}


def _rows_of(c, table, pk):
    return c.execute(text(f"SELECT {pk} FROM {table} LIMIT 1")).scalar()


@pytest.fixture(scope="module")
def boundary_rows(client, pg):
    """One row of each 0011-0013 table, made through the API: a grant (and its ISSUED event), a decision receipt, and a
    connector-level certification history row (a runtime-health change by a platform certifier, then reverted)."""
    from app.db import AdminSession
    from app.models import Actor
    from conftest import add_principal
    with AdminSession() as db:
        if db.get(Actor, "r039-certifier") is None:
            add_principal(db, actor_id="r039-certifier", tenant_id=None, actor_type="HUMAN", display_name="r039-certifier",
                          roles=["CONNECTOR_CERTIFICATION_MANAGER"])
        db.commit()
    scope = "r039 boundary " + secrets.token_hex(4)
    assert client.post("/api/ai/grants", headers=H(LEAD), json={
        "agent_subject_id": AGENT_SUBJECT, "action_class": "R039_BOUNDARY", "authority_level": "A1", "scope": scope,
        "reversibility": "r", "expires_in_days": 1}).status_code == 200
    assert client.post("/api/ai/decisions", headers=H(LEAD), json={
        "agent_subject_id": AGENT_SUBJECT, "action_class": "R039_BOUNDARY", "scope": scope,
        "requested_authority": "A1"}).status_code == 200
    snow = next(c for c in client.get("/api/connectors", headers=H("actor-admin")).json() if c["key"] == "servicenow-irm")
    url = f"/api/connectors/{snow['connector_id']}/certification"
    for health in ("FAILED", snow["runtime_health"]):
        assert client.post(url, headers=H("r039-certifier"), json={"certification": snow["certification"],
                                                                   "runtime_health": health, "reason": "r039"}).status_code == 200
    return True


def test_r039_cross_migration_privilege_boundary(client, pg, boundary_rows):
    """One matrix over the eight tables of 0011-0013 and every runtime context. 'ok:0' means RLS hid the row."""
    owner, runtime = pg
    with owner.connect() as c:
        grant = c.execute(text("SELECT grant_id FROM ai_authority_grant WHERE tenant_id = :t LIMIT 1"), {"t": TENANT}).scalar()
        gev = c.execute(text("SELECT event_id FROM ai_authority_grant_event WHERE tenant_id = :t LIMIT 1"), {"t": TENANT}).scalar()
        rec = c.execute(text("SELECT receipt_id FROM ai_policy_decision_receipt WHERE tenant_id = :t LIMIT 1"), {"t": TENANT}).scalar()
        conn_id = _rows_of(c, "integration_connector_definition", "connector_id")
        hev = _rows_of(c, "integration_connector_certification_event", "event_id")
        ext = _rows_of(c, "integration_extraction_contract_version", "extraction_contract_version_id")
        mp = _rows_of(c, "integration_mapping_contract_version", "mapping_contract_version_id")
        c.rollback()
    assert all((grant, gev, rec, conn_id, hev, ext, mp)), "earlier tests create one row of each"
    probes = [  # (label, sql, params)
        ("grant.update_status", "UPDATE ai_authority_grant SET status = status WHERE grant_id = :k", {"k": grant}),
        ("grant.update_scope", "UPDATE ai_authority_grant SET scope = scope WHERE grant_id = :k", {"k": grant}),
        ("grant.delete", "DELETE FROM ai_authority_grant WHERE grant_id = :k", {"k": grant}),
        ("grant_event.update", "UPDATE ai_authority_grant_event SET reason = reason WHERE event_id = :k", {"k": gev}),
        ("grant_event.delete", "DELETE FROM ai_authority_grant_event WHERE event_id = :k", {"k": gev}),
        ("receipt.update", "UPDATE ai_policy_decision_receipt SET decision = decision WHERE receipt_id = :k", {"k": rec}),
        ("receipt.delete", "DELETE FROM ai_policy_decision_receipt WHERE receipt_id = :k", {"k": rec}),
        ("registry.update_cert", "UPDATE integration_connector_definition SET certification = certification "
                                 "WHERE connector_id = :k", {"k": conn_id}),
        ("registry.update_build", "UPDATE integration_connector_definition SET build_version = build_version "
                                  "WHERE connector_id = :k", {"k": conn_id}),
        ("registry.delete", "DELETE FROM integration_connector_definition WHERE connector_id = :k", {"k": conn_id}),
        ("cert_history.update", "UPDATE integration_connector_certification_event SET reason = reason WHERE event_id = :k",
         {"k": hev}),
        ("cert_history.delete", "DELETE FROM integration_connector_certification_event WHERE event_id = :k", {"k": hev}),
        ("extraction.update_status", "UPDATE integration_extraction_contract_version SET status = status "
                                     "WHERE extraction_contract_version_id = :k", {"k": ext}),
        ("extraction.update_endpoint", "UPDATE integration_extraction_contract_version SET endpoint_id = endpoint_id "
                                       "WHERE extraction_contract_version_id = :k", {"k": ext}),
        ("mapping.delete", "DELETE FROM integration_mapping_contract_version WHERE mapping_contract_version_id = :k",
         {"k": mp}),
    ]
    expected = {  # per probe: outcome for tenant_user/ai_agent, platform_user, system_job, no_context
        "grant.update_status": ("ok:1", "ok:0", "ok:1", "ok:0"),   # status column; system scope sees tenant rows (I-01)
        "grant.update_scope": ("privilege",) * 4,
        "grant.delete": ("privilege",) * 4,
        "grant_event.update": ("privilege",) * 4,
        "grant_event.delete": ("privilege",) * 4,
        "receipt.update": ("privilege",) * 4,
        "receipt.delete": ("privilege",) * 4,
        "registry.update_cert": ("ok:0", "ok:1", "ok:0", "ok:0"),
        "registry.update_build": ("privilege",) * 4,
        "registry.delete": ("privilege",) * 4,
        "cert_history.update": ("privilege",) * 4,
        "cert_history.delete": ("privilege",) * 4,
        "extraction.update_status": ("ok:0", "ok:1", "ok:0", "ok:0"),
        "extraction.update_endpoint": ("privilege",) * 4,
        "mapping.delete": ("privilege",) * 4,
    }
    seen = {}
    for name, ctx in CONTEXTS.items():
        with runtime.connect() as c:
            tx = c.begin()
            if ctx is not None:
                stamp(c, ctx["tenant"], actor=ctx["actor"], flags=ctx.get("flags", ()))
            for label, sql, params in probes:
                seen[(name, label)] = _try(c, sql, params)
            tx.rollback()
    column = {"tenant_user": 0, "ai_agent": 0, "platform_user": 1, "system_job": 2, "no_context": 3}
    mismatches = [f"{n}/{lbl}: got {got}, expected {expected[lbl][column[n]]}" for (n, lbl), got in seen.items()
                  if got != expected[lbl][column[n]]]
    assert mismatches == []
    with owner.connect() as c:  # the table owner: the row triggers refuse a real change too (it could disable them: A10)
        tx = c.begin()
        for label, sql, params in (
                ("grant.update_scope", "UPDATE ai_authority_grant SET scope = scope || 'x' WHERE grant_id = :k", {"k": grant}),
                ("grant.delete", "DELETE FROM ai_authority_grant WHERE grant_id = :k", {"k": grant}),
                ("grant_event.update", "UPDATE ai_authority_grant_event SET reason = 'x' WHERE event_id = :k", {"k": gev}),
                ("receipt.update", "UPDATE ai_policy_decision_receipt SET decision = 'DENY' WHERE receipt_id = :k",
                 {"k": rec}),
                ("registry.update_build", "UPDATE integration_connector_definition SET build_version = build_version || 'x' "
                                          "WHERE connector_id = :k", {"k": conn_id}),
                ("registry.delete", "DELETE FROM integration_connector_definition WHERE connector_id = :k", {"k": conn_id}),
                ("cert_history.update", "UPDATE integration_connector_certification_event SET reason = 'x' "
                                        "WHERE event_id = :k", {"k": hev}),
                ("extraction.update_endpoint", "UPDATE integration_extraction_contract_version SET endpoint_id = 'x' "
                                               "WHERE extraction_contract_version_id = :k", {"k": ext}),
                ("mapping.delete", "DELETE FROM integration_mapping_contract_version "
                                   "WHERE mapping_contract_version_id = :k", {"k": mp})):
            assert _try(c, sql, params) == "trigger", label
        tx.rollback()
