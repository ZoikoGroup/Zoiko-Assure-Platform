"""Task 2: authentication, session lifecycle, outbox visibility and security audit (runs on SQLite and PostgreSQL).

ZA-IAM-001 §03 (generic errors, new session on sign-in), §05 (server-side revocation), §21 (identity and authorization
security events), §22 (iam.session); IAM-REQ-014/015 (current authority at commit), IAM-REQ-043 (no secrets in audit);
ZA-EVT-001 §19 (platform security events are not customer-visible); ZA-DAT-001 §12/§16.
The database-level proofs of the same properties are in tests/test_tenant_security.py (PostgreSQL only).
"""
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal

TENANT = "tenant-zoiko-group"
OTHER_TENANT = "tenant-t2-other"
USER, USER_EMAIL = "t2-user", "t2.user@test.example"
PW_USER, PW_EMAIL = "t2-pw-user", "t2.pw@test.example"
SUPER = "t2-super"
OTHER_USER = "t2-other-cco"


@pytest.fixture
def accounts(client):
    """A tenant user and a password-change user with their own credentials, a platform Super Admin (no tenant) and a
    second organisation, so these tests share no sign-in state with other modules."""
    from datetime import datetime, timezone
    from app.auth import DEMO_PASSWORD, hash_password
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, AuthCredential, AuthSession, Tenant

    test_actors = (USER, PW_USER, SUPER, OTHER_USER)

    def _revoke_sessions():
        with AdminSession() as db:
            now = datetime.now(timezone.utc)
            for s in db.execute(select(AuthSession).where(
                AuthSession.actor_id.in_(test_actors),
                AuthSession.revoked_at.is_(None),
            )).scalars():
                s.revoked_at = now
            db.commit()

    with AdminSession() as db:
        if db.get(Tenant, OTHER_TENANT) is None:
            db.add(Tenant(tenant_id=OTHER_TENANT, legal_name="T2 Other Group", contracting_region=CELL_ID))
            db.flush()
        for aid, tenant, email, roles in ((USER, TENANT, USER_EMAIL, ["CCO"]), (PW_USER, TENANT, PW_EMAIL, ["CCO"]),
                                          (SUPER, None, "t2.super@test.example", ["SUPER_ADMIN"]),
                                          (OTHER_USER, OTHER_TENANT, "t2.other@test.example", ["CCO"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, email=email,
                              roles=roles)
            cred = db.get(AuthCredential, aid) or AuthCredential(actor_id=aid)
            cred.password_hash, cred.failed_attempts, cred.locked_until, cred.must_change = (
                hash_password(DEMO_PASSWORD), 0, None, False)
            db.merge(cred)
        now = datetime.now(timezone.utc)
        for s in db.execute(select(AuthSession).where(
            AuthSession.actor_id.in_(test_actors),
            AuthSession.revoked_at.is_(None),
        )).scalars():
            s.revoked_at = now
        db.commit()

    yield DEMO_PASSWORD

    _revoke_sessions()


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _latest(action, **where):
    from app.db import AdminSession
    from app.models import AuditEvent
    q = select(AuditEvent).where(AuditEvent.action == action)
    for k, v in where.items():
        q = q.where(getattr(AuditEvent, k) == v)
    with AdminSession() as db:
        return db.execute(q.order_by(AuditEvent.record_seq.desc())).scalars().first()


# ------------------------------------------------------------------------------------------------ tokens and sessions
def test_only_a_hash_of_the_session_token_is_stored(client, accounts):
    from app.db import AdminSession
    from app.models import AuthSession
    from app.platform import to_dict
    token = _login(client, USER_EMAIL, accounts).json()["token"]
    with AdminSession() as db:
        row = db.execute(select(AuthSession).where(
            AuthSession.token_hash == hashlib.sha256(token.encode()).hexdigest())).scalars().one()
        assert row.actor_id == USER
        stored = to_dict(row)
    assert token not in repr(stored)  # neither the token nor anything it is built from
    assert row.session_id not in token and USER not in token
    assert client.get("/api/auth/session", headers=_bearer(token)).json()["actor"]["actor_id"] == USER


def test_every_sign_in_gets_a_new_session_and_token(client, accounts):
    a, b = (_login(client, USER_EMAIL, accounts).json()["token"] for _ in range(2))
    assert a != b
    from app.db import AdminSession
    from app.models import AuthSession
    with AdminSession() as db:
        ids = {db.execute(select(AuthSession.session_id).where(AuthSession.token_hash == hashlib.sha256(t.encode())
                                                               .hexdigest())).scalar_one() for t in (a, b)}
    assert len(ids) == 2


def test_forged_unknown_old_format_and_revoked_tokens_are_refused(client, accounts):
    token = _login(client, USER_EMAIL, accounts).json()["token"]
    old_format = "eyJzaWQiOiJ4Iiwic3ViIjoiYWN0b3ItY2NvIiwiZXhwIjo0MTAyNDQ0ODAwfQ.c2lnbmF0dXJl"
    for bad in (token[:-2] + ("AA" if not token.endswith("AA") else "BB"), "x" * 43, old_format, "y" * 500):
        assert client.get("/api/me", headers=_bearer(bad)).status_code == 401, bad[:20]
    assert client.get("/api/me", headers=_bearer(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 200
    assert client.get("/api/me", headers=_bearer(token)).status_code == 401
    assert _latest("auth.logout", actor_id=USER) is not None


# ------------------------------------------------------------------------------------------------ sign-in failures
def test_failed_sign_ins_get_one_generic_answer_and_a_reason_in_the_audit_only(client, accounts):
    unknown_email = "no.such.person@test.example"
    answers = [_login(client, USER_EMAIL, "Wrong-password-1"),          # wrong password
               _login(client, unknown_email, "Wrong-password-1"),       # no such account
               _login(client, "actor-agent@zoiko.example", accounts)]   # agents never sign in (INV-01)
    assert {r.status_code for r in answers} == {401}
    assert len({r.text for r in answers}) == 1  # nothing tells the caller which case it was (ZA-IAM-001 §03)
    assert _latest("auth.login_failed", actor_id=USER).reason == "BAD_PASSWORD"
    assert _latest("auth.login_failed", actor_id="actor-agent").reason == "NOT_INTERACTIVE"
    attempt = _latest("auth.login_failed", object_type="LoginAttempt")
    assert attempt.reason == "UNKNOWN_ACCOUNT" and attempt.tenant_id is None and attempt.actor_id is None
    assert attempt.object_id.startswith("email-sha256:")
    assert unknown_email not in repr([attempt.object_id, attempt.reason, attempt.prior_state, attempt.new_state])
    # the passwords tried never reach the audit trail (IAM-REQ-043)
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        rows = db.execute(select(AuditEvent).where(AuditEvent.action.like("auth.%"))).scalars().all()
    assert not any("Wrong-password-1" in repr(vars(r)) or accounts in repr(vars(r)) for r in rows)


def test_password_change_failures_and_revocations_are_audited(client, accounts):
    from app.auth import generate_password
    token = _login(client, PW_EMAIL, accounts).json()["token"]
    other = _login(client, PW_EMAIL, accounts).json()["token"]
    wrong = client.post("/api/auth/password", headers=_bearer(token),
                        json={"current_password": "Not-the-password-9", "new_password": generate_password()})
    assert wrong.status_code == 403
    assert _latest("auth.password_change_failed", actor_id=PW_USER).reason == "BAD_CURRENT_PASSWORD"
    ok = client.post("/api/auth/password", headers=_bearer(token),
                     json={"current_password": accounts, "new_password": generate_password()})
    assert ok.status_code == 200
    assert _latest("auth.password_changed", actor_id=PW_USER).reason == "1 other session(s) revoked"
    assert client.get("/api/me", headers=_bearer(other)).status_code == 401
    assert client.get("/api/me", headers=_bearer(token)).status_code == 200


# ------------------------------------------------------------------------------------------------ revocation at commit
def test_writes_authorised_by_a_session_revoked_before_commit_are_refused(client, accounts):
    """IAM-REQ-014/015: authority is checked again at commit, not only when the request started."""
    from app.auth import resolve_token
    from app.db import AdminSession, SessionLocal, capability
    from app.models import AuditEvent
    from app.platform import audit
    token = _login(client, USER_EMAIL, accounts).json()["token"]
    db = SessionLocal()
    try:
        with capability(db, "auth"):  # the request authenticates, as get_ctx does ...
            actor, sess = resolve_token(db, token)
        db.info["revalidate_session"] = True
        db.rollback()
        assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 200  # ... is revoked meanwhile ...
        audit(db, action="t2.in_flight_probe", object_type="Probe", object_id=sess.session_id, actor=actor,
              tenant_id=actor.tenant_id)
        with pytest.raises(HTTPException) as refused:  # ... and its write cannot commit
            db.commit()
        assert refused.value.status_code == 401
    finally:
        db.close()
    with AdminSession() as s:
        assert s.execute(select(AuditEvent).where(AuditEvent.action == "t2.in_flight_probe")).first() is None


def test_writes_under_a_live_session_still_commit(client, accounts):
    token = _login(client, USER_EMAIL, accounts).json()["token"]
    r = client.post("/api/org/subjects", headers=_bearer(token),
                    json={"entity_id": "entity-uk", "subject_type": "BUSINESS_UNIT", "display_name": "T2 Probe Unit"})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------------ outbox visibility
def test_outbox_returns_only_the_callers_own_events(client, accounts):
    """The no-tenant platform events of a failed publication, and other organisations' events, are never returned."""
    from app.db import AdminSession
    from app.platform import emit
    with AdminSession() as db:
        platform_ev = emit(db, event_type="security.content_signature_failed", aggregate_type="ContentRelease",
                           aggregate_id="t2-outbox-probe", payload={"probe": "platform"})
        other_ev = emit(db, event_type="entity.registered", aggregate_type="LegalEntity", aggregate_id="t2-outbox-probe",
                        tenant_id=OTHER_TENANT, payload={"probe": "other"})
        own_ev = emit(db, event_type="entity.registered", aggregate_type="LegalEntity", aggregate_id="t2-outbox-probe",
                      tenant_id=TENANT, payload={"probe": "own"})
        db.commit()
    rows = client.get("/api/outbox", params={"limit": 10000}, headers=H("actor-cco")).json()
    ids = {e["event_id"] for e in rows}
    assert own_ev.event_id in ids
    assert platform_ev.event_id not in ids and other_ev.event_id not in ids
    assert {e["tenant_id"] for e in rows} == {TENANT}
    assert not any(e["event_type"].startswith("security.") and e["tenant_id"] is None for e in rows)
    # platform staff have no tenant outbox to read, and the refusal is recorded
    assert client.get("/api/outbox", headers=H(SUPER)).status_code == 403


# ------------------------------------------------------------------------------------------------ security audit
def test_refusals_are_recorded_as_authorization_denied(client, accounts):
    assert client.get("/api/evidence", headers=H(SUPER)).status_code == 403
    ev = _latest("authorization.denied", actor_id=SUPER)
    # Stage 4a: the read guard refuses first (the Super Admin holds no read permission), before any tenant scope
    assert (ev.tenant_id, ev.new_state, ev.object_id) == (None, "MISSING_PERMISSION", "GET /api/evidence")
    # an organisation user trying a platform function: recorded under that organisation, visible to it
    assert client.get("/api/platform/organisations", headers=H("actor-cco")).status_code == 403
    ev = _latest("authorization.denied", actor_id="actor-cco")
    assert (ev.tenant_id, ev.new_state, ev.object_id) == (TENANT, "PLATFORM_FUNCTION", "GET /api/platform/organisations")
    assert any(e["id"] == ev.id for e in client.get("/api/audit", headers=H("actor-cco")).json())
    # and never in another organisation's trail
    assert not any(e["id"] == ev.id for e in client.get("/api/audit", headers=H(OTHER_USER)).json())


def test_super_admin_registry_reads_are_audited(client, accounts):
    assert client.get("/api/platform/organisations", headers=H(SUPER)).status_code == 200
    listed = _latest("platform.organisation_registry_read", actor_id=SUPER)
    assert listed is not None and listed.tenant_id is None  # a platform record: no organisation sees it
    assert client.get(f"/api/platform/organisations/{TENANT}", headers=H(SUPER)).status_code == 200
    viewed = _latest("platform.organisation_viewed", actor_id=SUPER)
    assert (viewed.tenant_id, viewed.object_id) == (TENANT, TENANT)
    # the organisation sees that the platform looked at it; other organisations do not
    assert any(e["id"] == viewed.id for e in client.get("/api/audit", headers=H("actor-cco")).json())
    assert not any(e["id"] in (viewed.id, listed.id) for e in client.get("/api/audit", headers=H(OTHER_USER)).json())
