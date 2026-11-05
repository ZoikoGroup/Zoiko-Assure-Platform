"""Stage 3c: agent authority grants keep an append-only, audited history (migration 0011).

Requirements (checked against the specification text):
- IAM-INV-09: authorisation policies and role/grant changes are versioned and auditable.
- IAM-REQ-030: agent authority is an explicit grant per action class + scope + expiry + authority level.
- IAM-REQ-015 / IAM-INV-07: revoking a grant invalidates new authorisation immediately; IAM §05: prior grants are not
  automatically restored (REVOKED is final).
- ZA-IAM-001 §21: authority_grant.issued / revoked carry grant, action/authority, scope, expiry, grantor/approver.
- ZA-EVT-001 catalogue: authority_grant.issued, authority_grant.revoked; EVT-REQ-001 / DAT-REQ-014 (outbox in the same
  transaction); EVT-REQ-008 (registered event types only); EVT-REQ-025 (minimised payload).
- DAT-REQ-037 (no hard delete of governed history), SEC-REQ-029 (immutability protections), DAT-REQ-003 (FORCE RLS),
  DAT-REQ-028 / SEC-REQ-008 (cross-tenant negative tests), IAM-REQ-033 / INV-01 (no workload or agent grants authority).

The PostgreSQL tests talk to the database directly as the runtime role (DATABASE_URL), below every application check.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from conftest import H, add_principal, stamp

TENANT = "tenant-zoiko-group"
# A second organisation of this module's own: tests/test_authorization.py creates its principals only when
# "tenant-authz-b" does not exist yet, so reusing that tenant from an earlier module would break it.
OTHER = "tenant-grant-b"
AGENT_SUBJECT = "subj-ai-payables"          # the seeded agent subject of Customer Zero
LEAD = "actor-aigov"                       # HUMAN, AI_GOVERNANCE_LEAD: grant_authority + request_policy_decision
OTHER_LEAD = "grant-b-aigov"               # the same role in another organisation


@pytest.fixture(scope="module")
def other_org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        if db.get(Tenant, OTHER) is None:
            db.add(Tenant(tenant_id=OTHER, legal_name="Grant Probe Group B", contracting_region="UK"))
            db.flush()
        if db.get(LegalEntity, "entity-grant-b") is None:
            db.add(LegalEntity(entity_id="entity-grant-b", tenant_id=OTHER, legal_name="Grant Probe Ltd", country="GB",
                               home_cell_id=CELL_ID, subject_id="subj-grant-b"))
        if db.get(Subject, "subj-grant-b-agent") is None:
            db.add(Subject(subject_id="subj-grant-b-agent", tenant_id=OTHER, entity_id="entity-grant-b",
                           subject_type="AI_ASSET", display_name="Tenant B agent"))
        if db.get(Actor, OTHER_LEAD) is None:
            add_principal(db, actor_id=OTHER_LEAD, tenant_id=OTHER, actor_type="HUMAN", display_name=OTHER_LEAD,
                          roles=["AI_GOVERNANCE_LEAD"])
        db.commit()
    return True


def _issue(client, actor=LEAD, subject=AGENT_SUBJECT, **over):
    scope = over.pop("scope", "probe scope " + secrets.token_hex(4))
    body = {"agent_subject_id": subject, "action_class": "PREPARE_FILING_DRAFT", "authority_level": "A2",
            "scope": scope, "reversibility": "Reversible before submission", "expires_in_days": 30, **over}
    return client.post("/api/ai/grants", headers=H(actor), json=body)


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _decide(client, grant):
    r = client.post("/api/ai/decisions", headers=H(LEAD), json={
        "agent_subject_id": grant["agent_subject_id"], "action_class": grant["action_class"], "scope": grant["scope"],
        "requested_authority": "A2"})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ both databases
def test_issuing_a_grant_records_projection_event_audit_and_outbox_together(client):
    from app.models import AuditEvent, AuthorityGrant, AuthorityGrantEvent, OutboxEvent
    r = _issue(client)
    assert r.status_code == 200, r.text
    g = r.json()
    (ev,) = _rows(AuthorityGrantEvent, grant_id=g["grant_id"])
    assert (ev.event_type, ev.from_status, ev.to_status, ev.actor_id, ev.tenant_id) == ("ISSUED", None, "ACTIVE", LEAD, TENANT)
    assert (ev.action_class, ev.authority_level, ev.scope) == (g["action_class"], "A2", g["scope"])
    (proj,) = _rows(AuthorityGrant, grant_id=g["grant_id"])
    assert (proj.status, proj.granted_by) == ("ACTIVE", LEAD)
    (a,) = _rows(AuditEvent, object_id=g["grant_id"])
    assert (a.action, a.actor_id, a.new_state, a.record_seq, a.tenant_id) == (
        "authority_grant.issued", LEAD, "ACTIVE", ev.record_seq, TENANT)
    for part in ("PREPARE_FILING_DRAFT", "authority=A2", g["scope"], "expires_at="):  # IAM §21 minimum content
        assert part in a.reason
    (o,) = [o for o in _rows(OutboxEvent, aggregate_id=g["grant_id"])]
    assert (o.event_type, o.aggregate_type, o.record_seq, o.tenant_id) == (
        "authority_grant.issued", "AuthorityGrant", ev.record_seq, TENANT)
    assert set(o.payload) == {"grant_id", "agent_subject_id", "authority_level", "expires_at"}  # no free text
    assert _decide(client, g)["decision"] == "PERMIT"


def test_revocation_is_recorded_atomically_and_stops_the_next_decision(client):
    from app.models import AuditEvent, AuthorityGrantEvent, OutboxEvent
    g = _issue(client).json()
    assert _decide(client, g)["decision"] == "PERMIT"
    r = client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD), json={"reason": "agent retired"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REVOKED"
    events = {e.event_type: e for e in _rows(AuthorityGrantEvent, grant_id=g["grant_id"])}
    rev = events["REVOKED"]
    assert (rev.from_status, rev.to_status, rev.actor_id, rev.reason) == ("ACTIVE", "REVOKED", LEAD, "agent retired")
    assert rev.record_seq > events["ISSUED"].record_seq
    audits = {x.action: x for x in _rows(AuditEvent, object_id=g["grant_id"])}
    ra = audits["authority_grant.revoked"]
    assert (ra.prior_state, ra.new_state, ra.record_seq, ra.actor_id) == ("ACTIVE", "REVOKED", rev.record_seq, LEAD)
    assert "reason=agent retired" in ra.reason and g["scope"] in ra.reason
    out = {o.event_type: o for o in _rows(OutboxEvent, aggregate_id=g["grant_id"])}
    assert out["authority_grant.revoked"].record_seq == rev.record_seq
    assert "agent retired" not in str(out["authority_grant.revoked"].payload)  # EVT-REQ-025: no reason text
    d = _decide(client, g)  # IAM-REQ-015: the very next decision
    assert d["decision"] == "DENY" and d["grant_id"] is None


def test_revocation_is_idempotent_and_records_nothing_twice(client):
    from app.models import AuditEvent, AuthorityGrantEvent, OutboxEvent
    g = _issue(client).json()
    assert client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD)).status_code == 200  # no body
    counts = lambda: (len(_rows(AuthorityGrantEvent, grant_id=g["grant_id"])),  # noqa: E731
                      len(_rows(AuditEvent, object_id=g["grant_id"])), len(_rows(OutboxEvent, aggregate_id=g["grant_id"])))
    first = counts()
    assert first == (2, 2, 2)
    again = client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD), json={"reason": "retry"})
    assert again.status_code == 200 and again.json()["status"] == "REVOKED"
    assert counts() == first
    (rev,) = [e for e in _rows(AuthorityGrantEvent, grant_id=g["grant_id"]) if e.event_type == "REVOKED"]
    assert rev.reason is None  # the first (body-less) revocation's record stands


def test_revoke_body_is_strict(client):
    g = _issue(client).json()
    r = client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD), json={"reason": "x", "status": "ACTIVE"})
    assert r.status_code == 422  # API-REQ-014
    from app.models import AuthorityGrant
    assert _rows(AuthorityGrant, grant_id=g["grant_id"])[0].status == "ACTIVE"


def test_another_organisation_cannot_see_or_revoke_a_grant(client, other_org):
    from test_authorization import db_fingerprint
    g = _issue(client).json()
    before = db_fingerprint()
    r = client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(OTHER_LEAD), json={"reason": "hostile"})
    assert r.status_code == 404  # masked exactly like a missing grant
    assert client.post("/api/ai/grants/no-such-grant/revoke", headers=H(OTHER_LEAD)).status_code == 404
    assert _issue(client, actor=OTHER_LEAD).status_code == 404  # tenant A's agent subject does not exist for tenant B
    assert db_fingerprint() == before
    assert _decide(client, g)["decision"] == "PERMIT"  # untouched


def test_workloads_and_agents_cannot_issue_or_revoke(client):
    from test_authorization import db_fingerprint
    g = _issue(client).json()
    before = db_fingerprint()
    for actor in ("actor-svc", "actor-agent"):  # SERVICE; AGENT (over-privileged CCO roles: still refused, INV-01)
        assert _issue(client, actor=actor).status_code == 403, actor
        assert client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(actor)).status_code == 403, actor
    assert db_fingerprint() == before


@pytest.mark.parametrize("step", ["issue", "revoke"])
def test_a_failed_write_leaves_no_projection_event_audit_or_outbox(client, monkeypatch, step):
    """DAT-REQ-014: the outbox insert is the last write; when it fails, nothing of the transition survives."""
    from test_authorization import db_fingerprint
    from app.engines import governance as gov
    g = _issue(client).json() if step == "revoke" else None
    before = db_fingerprint()

    def boom(*a, **k):
        raise RuntimeError("injected outbox failure")
    monkeypatch.setattr(gov, "emit", boom)
    with pytest.raises(RuntimeError, match="injected"):
        if step == "issue":
            _issue(client)
        else:
            client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD), json={"reason": "probe"})
    assert db_fingerprint() == before
    monkeypatch.undo()
    if g:  # the grant is still ACTIVE and can be revoked normally afterwards
        assert _decide(client, g)["decision"] == "PERMIT"
        assert client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD)).json()["status"] == "REVOKED"


def test_grant_status_is_written_only_by_the_event_writer():
    """Roadmap Stage 3 acceptance (grep gate): no application module other than the governance engine creates a grant
    or assigns its status; the demo seed is the one documented exception (synthetic fixture, no event history)."""
    import re
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for p in app.rglob("*.py"):
        if p.name in ("governance.py", "models.py", "seed.py"):
            continue
        src = p.read_text(encoding="utf-8")
        if re.search(r"AuthorityGrant\(", src) or re.search(r"\bg\.status\s*=|grant\.status\s*=", src):
            offenders.append(p.name)
    assert offenders == []
    assert "agent.grant_issued" not in (app / "routers" / "ai.py").read_text(encoding="utf-8")  # EVT-REQ-008


# ------------------------------------------------------------------------------------------------ PostgreSQL below the API
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES, admin_engine, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine, engine


def _refused(conn, sql, params, match):
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError, match=match):
        conn.execute(text(sql), params)
    sp.rollback()


def test_runtime_privileges_triggers_and_force_rls(pg):
    owner, _ = pg
    expect = {  # table: (UPDATE, DELETE, updatable columns, required triggers)
        "ai_authority_grant": (False, False, {"status"}, {"immutable_columns", "lifecycle_final"}),
        "ai_authority_grant_event": (False, False, set(), {"append_only"}),
        "ai_policy_decision_receipt": (False, False, set(), {"append_only"}),
    }
    with owner.connect() as c:
        for t, (u, d, cols, trig) in expect.items():
            upd, dele, ins, sel, trunc = c.execute(text(
                "SELECT has_table_privilege('assure_app', :t, 'UPDATE'), has_table_privilege('assure_app', :t, 'DELETE'), "
                "has_table_privilege('assure_app', :t, 'INSERT'), has_table_privilege('assure_app', :t, 'SELECT'), "
                "has_table_privilege('assure_app', :t, 'TRUNCATE')"), {"t": t}).one()
            granted = set(c.execute(text(
                "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 "
                "AND NOT a.attisdropped AND has_column_privilege('assure_app', a.attrelid, a.attnum, 'UPDATE')"),
                {"t": t}).scalars())
            triggers = set(c.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) "
                                          "AND NOT tgisinternal AND tgenabled = 'O'"), {"t": t}).scalars())
            forced = c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                                    "WHERE oid = CAST(:t AS regclass)"), {"t": t}).scalar()
            assert (upd, dele, granted, sel, ins, trunc) == (u, d, cols, True, True, False), t
            assert trig <= triggers and forced, t


def test_a_revoked_grant_cannot_be_reactivated_widened_or_deleted(client, pg):
    owner, runtime = pg
    g = _issue(client).json()
    assert client.post(f"/api/ai/grants/{g['grant_id']}/revoke", headers=H(LEAD), json={"reason": "probe"}).status_code == 200
    k = {"g": g["grant_id"]}
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        _refused(c, "UPDATE ai_authority_grant SET status = 'ACTIVE' WHERE grant_id = :g", k, "is final once status is REVOKED")
        _refused(c, "UPDATE ai_authority_grant SET expires_at = expires_at + interval '1 year' WHERE grant_id = :g", k,
                 "permission denied")
        _refused(c, "UPDATE ai_authority_grant SET authority_level = 'A3' WHERE grant_id = :g", k, "permission denied")
        _refused(c, "DELETE FROM ai_authority_grant WHERE grant_id = :g", k, "permission denied")
        _refused(c, "UPDATE ai_authority_grant_event SET reason = 'rewritten' WHERE grant_id = :g", k, "permission denied")
        _refused(c, "DELETE FROM ai_authority_grant_event WHERE grant_id = :g", k, "permission denied")
        tx.rollback()
    with owner.connect() as c:  # the owner role is refused by the row triggers as well
        tx = c.begin()
        _refused(c, "UPDATE ai_authority_grant SET scope = 'everything' WHERE grant_id = :g", k, "append-only except")
        _refused(c, "DELETE FROM ai_authority_grant WHERE grant_id = :g", k, "append-only")
        _refused(c, "UPDATE ai_authority_grant SET status = 'ACTIVE' WHERE grant_id = :g", k, "is final once status is REVOKED")
        _refused(c, "UPDATE ai_authority_grant_event SET reason = 'rewritten' WHERE grant_id = :g", k, "append-only")
        _refused(c, "DELETE FROM ai_authority_grant_event WHERE grant_id = :g", k, "append-only")
        tx.rollback()


def test_an_active_grant_changes_only_through_its_status_column(client, pg):
    _, runtime = pg
    g = _issue(client).json()
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        assert c.execute(text("UPDATE ai_authority_grant SET status = 'REVOKED' WHERE grant_id = :g"),
                         {"g": g["grant_id"]}).rowcount == 1  # what the event writer does
        tx.rollback()
    assert _decide(client, g)["decision"] == "PERMIT"  # rolled back: still active


def test_grant_history_is_tenant_isolated_below_the_api(client, pg, other_org):
    _, runtime = pg
    g = _issue(client).json()
    k = {"g": g["grant_id"]}
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, OTHER)
        assert c.execute(text("SELECT count(*) FROM ai_authority_grant_event WHERE grant_id = :g"), k).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM ai_authority_grant WHERE grant_id = :g"), k).scalar() == 0
        assert c.execute(text("UPDATE ai_authority_grant SET status = 'REVOKED' WHERE grant_id = :g"), k).rowcount == 0
        # WITH CHECK: an event for another organisation's tenant cannot be inserted from this scope
        _refused(c, "INSERT INTO ai_authority_grant_event (event_id, grant_id, tenant_id, agent_subject_id, event_type, "
                    "to_status, action_class, authority_level, scope, expires_at, actor_id, record_seq, created_at) "
                    "VALUES ('za-probe', :g, :t, 'x', 'REVOKED', 'REVOKED', 'x', 'A0', 'x', now(), 'x', 1, now())",
                 {**k, "t": TENANT}, "row-level security")
        stamp(c, TENANT)
        assert c.execute(text("SELECT count(*) FROM ai_authority_grant_event WHERE grant_id = :g"), k).scalar() == 1
        tx.rollback()
    with runtime.connect() as c:  # no tenant context at all: nothing is visible
        assert c.execute(text("SELECT count(*) FROM ai_authority_grant_event")).scalar() == 0
        c.rollback()


def test_concurrent_revocations_record_exactly_one_event(client, pg):
    """Two transactions revoke the same grant: the row lock serialises them and the second sees REVOKED (idempotent)."""
    import threading
    from app.db import SessionLocal
    from app.engines import governance as gov
    from app.models import AuthorityGrantEvent
    from app.db import set_tenant_scope
    g = _issue(client).json()
    barrier, errors = threading.Barrier(2), []

    def worker():
        try:
            with SessionLocal() as db:
                set_tenant_scope(db, TENANT)
                barrier.wait()
                gov.revoke_authority_grant(db, tenant_id=TENANT, grant_id=g["grant_id"], actor=LEAD, reason="race")
                db.commit()
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert [e.event_type for e in _rows(AuthorityGrantEvent, grant_id=g["grant_id"])].count("REVOKED") == 1
