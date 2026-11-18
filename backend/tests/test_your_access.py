"""Users & Roles → Your Access: the signed-in principal's own account, role assignments, permissions and sessions.

Requirements: ZA-IAM-001 §05 (sessions and revocation), §22 (iam.session), §23 (GET /sessions, POST
/sessions/{id}/revoke: "self or authorized security admin"), IAM-REQ-010 (explicit scope), IAM-REQ-015 (authority read
from the registry), IAM-REQ-043 (no credential material in any view); WF-IAM-01/02.

The session check (GET /auth/session) and GET /me must describe the same access, each keeping its own contract; an
entity-scoped principal must see its entity assignment rather than an empty view; and a principal's session list holds
only its own live sessions, whichever kind of principal it is.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import denials

TA, TB, ENTITY = "tenant-ya", "tenant-ya-b", "entity-ya"
YA_SUPER, YA_TSA, YA_CCO, YA_ENTITY, YB_CCO = "ya-super", "ya-tsa", "ya-cco", "ya-entity", "ya-b-cco"
SESSION_ONLY = {"tenant_name", "must_change_password"}  # in the session check's actor, not in GET /me


@pytest.fixture(scope="module")
def people(client):
    """Two organisations; a Super Admin; in the first, a security admin, a CCO (with one expired extra assignment) and a
    principal whose only role is scoped to one legal entity, assigned through the role registry API."""
    from app.auth import DEMO_PASSWORD, hash_password
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, AuthCredential, LegalEntity, RoleAssignment, Subject, Tenant
    from app.platform import next_record_seq
    now = datetime.now(timezone.utc)
    with AdminSession() as db:
        for tid, name in ((TA, "Your Access Probe A"), (TB, "Your Access Probe B")):
            if db.get(Tenant, tid) is None:
                db.add(Tenant(tenant_id=tid, legal_name=name, contracting_region="UK"))
                db.flush()
        if db.get(LegalEntity, ENTITY) is None:
            db.add(LegalEntity(entity_id=ENTITY, tenant_id=TA, legal_name="Your Access Probe A Ltd", country="GB",
                               home_cell_id=CELL_ID, subject_id=f"subj-{ENTITY}"))
            db.add(Subject(subject_id=f"subj-{ENTITY}", tenant_id=TA, entity_id=ENTITY, subject_type="LEGAL_ENTITY",
                           display_name="Your Access Probe A Ltd"))
        for aid, tenant, roles in ((YA_SUPER, None, ["SUPER_ADMIN"]), (YA_TSA, TA, ["TENANT_SECURITY_ADMIN"]),
                                   (YA_CCO, TA, ["CCO"]), (YA_ENTITY, TA, []), (YB_CCO, TB, ["CCO"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=f"Probe {aid}",
                              email=f"{aid}@your-access.example", roles=roles)
            db.flush()
            if db.get(AuthCredential, aid) is None:
                db.add(AuthCredential(actor_id=aid, password_hash=hash_password(DEMO_PASSWORD)))
        expired = select(RoleAssignment).where(RoleAssignment.principal_id == YA_CCO,
                                               RoleAssignment.role_code == "RISK_APPROVER")
        if db.execute(expired).first() is None:  # held yesterday, ended an hour ago: confers nothing now
            db.add(RoleAssignment(tenant_id=TA, principal_id=YA_CCO, role_code="RISK_APPROVER", scope_type="TENANT",
                                  scope_id=TA, valid_from=now - timedelta(days=1), valid_to=now - timedelta(hours=1),
                                  status="ACTIVE", source="TEST", reason="expired probe", record_seq=next_record_seq(db)))
        db.commit()
        entity_role = db.execute(select(RoleAssignment).where(RoleAssignment.principal_id == YA_ENTITY,
                                                              RoleAssignment.status == "ACTIVE")).first()
    if entity_role is None:
        r = client.post("/api/role-assignments", headers=H(YA_TSA), json={
            "principal_id": YA_ENTITY, "role_code": "INTERNAL_AUDIT", "scope_type": "LEGAL_ENTITY", "scope_id": ENTITY,
            "reason": "your access probe"})
        assert r.status_code == 201, r.text
    return True


def _login(client, actor_id) -> str:
    from app.auth import DEMO_PASSWORD
    r = client.post("/api/auth/login", json={"email": f"{actor_id}@your-access.example", "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _current_session_id(client, token) -> str:
    return next(s["session_id"] for s in client.get("/api/sessions", headers=_bearer(token)).json() if s["current"])


def _expired_session(actor_id) -> tuple[str, str]:
    """A session that ended an hour ago, and the bearer token that named it."""
    from app.auth import issue_token
    from app.db import AdminSession
    from app.models import AuthSession
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with AdminSession() as db:
        s = AuthSession(actor_id=actor_id, created_at=past - timedelta(hours=8), expires_at=past, user_agent="expired probe")
        token = issue_token(s)
        db.add(s)
        db.flush()
        sid = s.session_id
        db.commit()
    return sid, token


def _latest_audit(action, **where):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        q = select(AuditEvent).where(AuditEvent.action == action)
        for k, v in where.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return db.execute(q.order_by(AuditEvent.record_seq.desc())).scalars().first()


def _views(client, actor_id) -> tuple[dict, dict]:
    tok = _login(client, actor_id)
    session = client.get("/api/auth/session", headers=_bearer(tok))
    me = client.get("/api/me", headers=_bearer(tok))
    assert session.status_code == me.status_code == 200, (session.text, me.text)
    return session.json()["actor"], me.json()


def _scoped(view) -> list[tuple]:
    return [(a["role_code"], a["scope_type"], a["scope_id"], a["in_force"]) for a in view["role_assignments"]]


# ------------------------------------------------------------------------------------------------ account and access
def test_session_check_and_me_report_the_same_access_for_every_kind_of_principal(client, people):
    from app.models import Actor
    me_keys = {c.name for c in Actor.__table__.columns} | {"roles", "permissions", "role_assignments"}
    for actor_id in (YA_SUPER, YA_CCO, YA_ENTITY):
        session, me = _views(client, actor_id)
        assert set(me) == me_keys, actor_id                      # GET /me keeps its contract
        assert set(session) == me_keys | SESSION_ONLY, actor_id  # and so does the session check
        for k in me_keys - {"role_assignments"}:
            assert session[k] == me[k], (actor_id, k)
        assert [{k: v for k, v in a.items() if k != "in_force"} for a in session["role_assignments"]] == \
               [{k: v for k, v in a.items() if k != "in_force"} for a in me["role_assignments"]], actor_id
        assert session["must_change_password"] is False


def test_access_and_session_views_never_carry_credential_material(client, people):
    from app.auth import DEMO_PASSWORD
    from app.auth import token_hash as hash_of
    for actor_id in (YA_SUPER, YA_CCO, YA_ENTITY):
        login = client.post("/api/auth/login", json={"email": f"{actor_id}@your-access.example", "password": DEMO_PASSWORD})
        assert login.status_code == 200
        body = login.json()
        tok = body.pop("token")  # the one place a token is returned: once, at sign-in
        reads = [body] + [client.get(p, headers=_bearer(tok)).json() for p in ("/api/auth/session", "/api/me", "/api/sessions")]
        for payload in reads:
            text = json.dumps(payload)
            assert tok not in text and hash_of(tok) not in text and DEMO_PASSWORD not in text, actor_id
            keys = set()

            def walk(v):
                if isinstance(v, dict):
                    for k, x in v.items():
                        keys.add(k)
                        walk(x)
                elif isinstance(v, list):
                    for x in v:
                        walk(x)
            walk(payload)
            assert not {k for k in keys if "token" in k or "hash" in k or ("password" in k and k != "must_change_password")}, keys


def test_an_entity_scoped_principal_sees_its_entity_assignment_and_permissions(client, people):
    from app.security import ROLE_PERMISSIONS
    session, me = _views(client, YA_ENTITY)
    for view in (session, me):
        assert view["roles"] == [] and view["permissions"] == []  # nothing organisation-wide
        assert _scoped(view) == [("INTERNAL_AUDIT", "LEGAL_ENTITY", ENTITY, True)]
        a = view["role_assignments"][0]
        assert a["permissions"] == sorted(ROLE_PERMISSIONS["INTERNAL_AUDIT"]) and a["permissions"]
        assert a["status"] == "ACTIVE" and a["source"] == "ADMIN" and a["granted_by"] == YA_TSA and a["valid_to"] is None
    assert session["tenant_id"] == TA and session["tenant_name"] == "Your Access Probe A"


def test_the_super_admin_sees_one_platform_assignment_and_its_permissions(client, people):
    from app.security import ROLE_PERMISSIONS
    session, me = _views(client, YA_SUPER)
    for view in (session, me):
        assert view["tenant_id"] is None and view["roles"] == ["SUPER_ADMIN"]
        assert view["permissions"] == sorted(ROLE_PERMISSIONS["SUPER_ADMIN"])
        assert _scoped(view) == [("SUPER_ADMIN", "PLATFORM", None, True)]
        assert view["role_assignments"][0]["tenant_id"] is None
    assert session["tenant_name"] == "Zoiko content/platform operations"


def test_an_expired_assignment_confers_and_shows_nothing(client, people):
    from app.security import ROLE_PERMISSIONS
    session, me = _views(client, YA_CCO)
    for view in (session, me):
        assert view["roles"] == ["CCO"] and _scoped(view) == [("CCO", "TENANT", TA, True)]
        assert view["permissions"] == sorted(ROLE_PERMISSIONS["CCO"])
        assert "RISK_APPROVER" not in json.dumps(view["role_assignments"])


# ------------------------------------------------------------------------------------------------ sessions
def test_session_lists_hold_only_the_principals_live_sessions(client, people):
    live = _login(client, YA_CCO)
    ended = _login(client, YA_CCO)
    ended_id = _current_session_id(client, ended)
    assert client.post("/api/auth/logout", headers=_bearer(ended)).status_code == 200
    expired_id, _ = _expired_session(YA_CCO)
    others = {_current_session_id(client, _login(client, a)) for a in (YA_TSA, YB_CCO, YA_SUPER, YA_ENTITY)}

    mine = client.get("/api/sessions", headers=_bearer(live))
    assert mine.status_code == 200
    rows = mine.json()
    ids = {s["session_id"] for s in rows}
    assert {s["principal_id"] for s in rows} == {YA_CCO}
    assert [s["session_id"] for s in rows if s["current"]] == [_current_session_id(client, live)]
    assert not ids & ({ended_id, expired_id} | others)
    now = datetime.now(timezone.utc)
    assert all(datetime.fromisoformat(s["expires_at"].replace("Z", "+00:00")) > now for s in rows)

    # The organisation's security administrator sees the same live sessions of its member, and nothing ended.
    listed = client.get(f"/api/principals/{YA_CCO}/sessions", headers=H(YA_TSA))
    assert listed.status_code == 200
    assert {s["session_id"] for s in listed.json()} == ids and not any(s["current"] for s in listed.json())


def test_the_super_admin_ends_only_its_own_other_sessions(client, people):
    s1, s2 = _login(client, YA_SUPER), _login(client, YA_SUPER)
    s1_id, s2_id = _current_session_id(client, s1), _current_session_id(client, s2)

    assert client.post(f"/api/sessions/{s1_id}/revoke", headers=_bearer(s1), json={}).status_code == 409  # this one
    r = client.post(f"/api/sessions/{s2_id}/revoke", headers=_bearer(s1), json={"reason": "not me"})
    assert r.status_code == 200 and r.json()["session_id"] == s2_id and r.json()["current"] is False
    assert client.get("/api/auth/session", headers=_bearer(s2)).status_code == 401  # takes effect at once
    assert client.post(f"/api/sessions/{s2_id}/revoke", headers=_bearer(s1), json={}).status_code == 409
    rec = _latest_audit("auth.session_revoked", object_id=s2_id)
    assert rec.actor_id == YA_SUPER and rec.tenant_id is None and rec.new_state == "REVOKED" and "not me" in rec.reason

    # An organisation user cannot end the Super Admin's session; the refusal is recorded and s1 survives.
    cco = _login(client, YA_CCO)
    before = denials(YA_CCO)
    assert client.post(f"/api/sessions/{s1_id}/revoke", headers=_bearer(cco), json={"reason": "probe"}).status_code == 403
    assert denials(YA_CCO) - before == 1
    assert client.get("/api/auth/session", headers=_bearer(s1)).status_code == 200


def test_an_expired_session_is_refused_and_cannot_be_ended(client, people):
    expired_id, expired_token = _expired_session(YA_CCO)
    assert client.get("/api/auth/session", headers=_bearer(expired_token)).status_code == 401
    assert client.get("/api/me", headers=_bearer(expired_token)).status_code == 401
    assert client.get("/api/sessions", headers=_bearer(expired_token)).status_code == 401
    live = _login(client, YA_CCO)
    r = client.post(f"/api/sessions/{expired_id}/revoke", headers=_bearer(live), json={})
    assert r.status_code == 409 and "already ended" in r.json()["detail"]
