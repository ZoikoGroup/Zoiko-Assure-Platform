"""Super Admin & Organisation Admin (ZA-IAM-001 §08 TENANT_OWNER, ZA-TEN-001, INV-14, IAM-REQ-030).

Covers the one-time .env bootstrap, sign-in, role boundaries, organisation create/edit/suspend/reactivate, tenant
isolation for a new organisation, and the audit trail. All passwords here are generated per run.
"""
import pytest
from sqlalchemy import func, select

from conftest import H


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(env):
    from app.accounts import bootstrap_accounts
    from app.db import AdminSession
    with AdminSession() as db:
        return bootstrap_accounts(db, env)


@pytest.fixture(scope="module")
def super_admin(client):
    from app.auth import generate_password
    pw = generate_password()
    _bootstrap({"ZA_BOOTSTRAP_SUPER_ADMIN_EMAIL": "platform.root@test.example", "ZA_BOOTSTRAP_SUPER_ADMIN_PASSWORD": pw})
    r = _login(client, "platform.root@test.example", pw)
    assert r.status_code == 200, r.text
    assert r.json()["actor"]["permissions"] == ["manage_organisations"]
    return r.json()["token"]


def test_bootstrap_is_one_time_and_never_resets_passwords(client):
    from app.auth import generate_password
    from app.db import AdminSession
    from app.models import Actor, AuthCredential
    first_pw, second_pw = generate_password(), generate_password()
    env = {"ZA_BOOTSTRAP_SUPER_ADMIN_EMAIL": "Boot.Super@Test.example", "ZA_BOOTSTRAP_SUPER_ADMIN_PASSWORD": first_pw,
           "ZA_BOOTSTRAP_ORG_ADMIN_EMAIL": "boot.owner@test.example", "ZA_BOOTSTRAP_ORG_ADMIN_PASSWORD": first_pw,
           "ZA_BOOTSTRAP_ORG_TENANT_ID": "tenant-zoiko-group"}
    assert all(line.endswith(": created") for line in _bootstrap(env))
    with AdminSession() as db:
        sa = db.execute(select(Actor).where(Actor.email == "boot.super@test.example")).scalars().one()
        owner = db.execute(select(Actor).where(Actor.email == "boot.owner@test.example")).scalars().one()
        assert (sa.tenant_id, sa.roles, sa.actor_type) == (None, ["SUPER_ADMIN"], "HUMAN")
        assert (owner.tenant_id, owner.roles) == ("tenant-zoiko-group", ["TENANT_OWNER"])
        for a in (sa, owner):
            h = db.get(AuthCredential, a.actor_id).password_hash
            assert h.startswith("scrypt$") and first_pw not in h  # stored as a hash only

    # A later start-up with different values changes nothing: no reset, no duplicate.
    lines = _bootstrap({**env, "ZA_BOOTSTRAP_SUPER_ADMIN_PASSWORD": second_pw, "ZA_BOOTSTRAP_ORG_ADMIN_PASSWORD": second_pw})
    assert all("already exists, left unchanged" in line for line in lines)
    with AdminSession() as db:
        assert db.execute(select(func.count()).select_from(Actor).where(Actor.email == "boot.super@test.example")).scalar() == 1
    assert _login(client, "boot.super@test.example", first_pw).status_code == 200
    assert _login(client, "boot.super@test.example", second_pw).status_code == 401
    assert _login(client, "boot.owner@test.example", first_pw).status_code == 200


def test_bootstrap_fails_closed(client):
    from app.auth import generate_password
    weak = _bootstrap({"ZA_BOOTSTRAP_SUPER_ADMIN_EMAIL": "weak.super@test.example", "ZA_BOOTSTRAP_SUPER_ADMIN_PASSWORD": "short"})
    assert "skipped (password does not meet policy)" in weak[0]
    unknown = _bootstrap({"ZA_BOOTSTRAP_ORG_ADMIN_EMAIL": "ghost.owner@test.example",
                          "ZA_BOOTSTRAP_ORG_ADMIN_PASSWORD": generate_password(), "ZA_BOOTSTRAP_ORG_TENANT_ID": "tenant-nope"})
    assert "skipped (organisation not found)" in unknown[0]
    assert _bootstrap({}) == []  # nothing configured, nothing created
    assert _login(client, "weak.super@test.example", "short").status_code == 401


def test_super_admin_manages_the_registry_but_never_customer_data(client, super_admin):
    r = client.get("/api/platform/organisations", headers=_auth(super_admin))
    assert r.status_code == 200
    cz = next(o for o in r.json() if o["tenant_id"] == "tenant-zoiko-group")
    assert cz["status"] == "ACTIVE" and cz["entities"] and cz["cells"]
    assert set(cz) == {"tenant_id", "legal_name", "status", "contracting_region", "isolation_tier", "created_at",
                       "entities", "cells", "admins"}  # registry metadata only
    for path in ("/api/evidence", "/api/dashboard", "/api/obligations", "/api/audit", "/api/org/tenant",
                 "/api/applicability/evaluations", "/api/exceptions"):
        assert client.get(path, headers=_auth(super_admin)).status_code == 403, path
    # A bootstrap appointment is part of the organisation's administrative history.
    from app.auth import generate_password
    _bootstrap({"ZA_BOOTSTRAP_ORG_ADMIN_EMAIL": "history.owner@test.example", "ZA_BOOTSTRAP_ORG_ADMIN_PASSWORD": generate_password(),
                "ZA_BOOTSTRAP_ORG_TENANT_ID": "tenant-zoiko-group"})
    detail = client.get("/api/platform/organisations/tenant-zoiko-group", headers=_auth(super_admin)).json()
    assert "account.bootstrap_created" in {e["action"] for e in detail["admin_events"]}
    assert "history.owner@test.example" in {a["email"] for a in detail["admins"]}


def test_customer_roles_cannot_use_platform_endpoints(client):
    body = {"legal_name": "Nope Ltd", "entity": {"legal_name": "Nope Ltd", "country": "GB"},
            "admin": {"display_name": "Nope", "email": "nope@test.example"}}
    for actor in ("actor-cco", "actor-admin", "actor-content-analyst", "actor-svc"):
        assert client.get("/api/platform/organisations", headers=H(actor)).status_code == 403, actor
        assert client.post("/api/platform/organisations", headers=H(actor), json=body).status_code == 403, actor


def test_no_api_path_grants_super_admin_or_chooses_placement(client, super_admin):
    base = {"legal_name": "Grant Probe Ltd", "entity": {"legal_name": "Grant Probe Ltd", "country": "GB"},
            "admin": {"display_name": "Probe", "email": "probe.owner@test.example"}}
    probes = [{**base, "admin": {**base["admin"], "roles": ["SUPER_ADMIN"]}},
              {**base, "entity": {**base["entity"], "home_cell_id": "uk-south-cell-b"}},
              {**base, "isolation_tier": "DEDICATED"}, {**base, "tenant_id": "tenant-zoiko-group"}]
    for body in probes:
        assert client.post("/api/platform/organisations", headers=_auth(super_admin), json=body).status_code == 422


def test_organisation_lifecycle_isolation_and_audit(client, super_admin):
    from app.auth import generate_password
    from app.db import CELL_ID
    body = {"legal_name": "Acme Fintech Holdings", "entity": {"legal_name": "Acme Payments Ltd", "country": "gb",
                                                             "registration_ref": "12345678"},
            "admin": {"display_name": "Ada Admin", "email": "Ada.Admin@Acme-Test.example"}}
    r = client.post("/api/platform/organisations", headers=_auth(super_admin), json=body)
    assert r.status_code == 201, r.text
    org = r.json()
    tid, temp = org["tenant_id"], org["initial_admin_password"]
    assert org["status"] == "ACTIVE" and org["isolation_tier"] == "POOLED" and org["cells"] == [CELL_ID]
    assert org["entities"][0]["country"] == "GB" and org["admins"][0]["email"] == "ada.admin@acme-test.example"
    assert client.post("/api/platform/organisations", headers=_auth(super_admin), json=body).status_code == 409  # same admin email

    # The Organisation Admin must replace the temporary password before using anything else.
    lr = _login(client, "ada.admin@acme-test.example", temp)
    assert lr.status_code == 200 and lr.json()["must_change_password"] is True
    tok = lr.json()["token"]
    blocked = client.get("/api/org/tenant", headers=_auth(tok))
    assert blocked.status_code == 403 and blocked.json()["detail"]["error"] == "PASSWORD_CHANGE_REQUIRED"
    new_pw = generate_password()
    assert client.post("/api/auth/password", headers=_auth(tok), json={"current_password": temp, "new_password": new_pw}).status_code == 200
    me = client.get("/api/auth/session", headers=_auth(tok)).json()["actor"]
    assert me["must_change_password"] is False and me["roles"] == ["TENANT_OWNER"] and me["tenant_id"] == tid

    # Tenant isolation: the new organisation sees only itself; Customer Zero's records do not exist for it.
    assert client.get("/api/org/tenant", headers=_auth(tok)).json()["tenant"]["legal_name"] == "Acme Fintech Holdings"
    assert client.get("/api/evidence", headers=_auth(tok)).json() == []
    assert client.get("/api/obligations", headers=_auth(tok)).json() == []
    cz_evidence = client.get("/api/evidence", headers=H("actor-cco")).json()[0]["evidence_id"]
    assert client.get(f"/api/evidence/{cz_evidence}", headers=_auth(tok)).status_code == 404
    # TENANT_OWNER administers its own organisation (manage_tenant) but cannot attest or approve (SoD).
    ent = client.post("/api/org/entities", headers=_auth(tok), json={"legal_name": "Acme Labs Ltd", "country": "IE"})
    assert ent.status_code == 200 and ent.json()["tenant_id"] == tid
    assert "attest" not in me["permissions"] and "approve_exception" not in me["permissions"]

    # Audit: platform changes are recorded under the organisation and visible only to it.
    own = {e["action"] for e in client.get("/api/audit", headers=_auth(tok)).json()}
    assert {"organisation.created", "organisation.admin_appointed"} <= own
    assert not any(e["object_id"] == tid for e in client.get("/api/audit", headers=H("actor-cco")).json())

    # Edit
    ed = client.patch(f"/api/platform/organisations/{tid}", headers=_auth(super_admin), json={"legal_name": "Acme Fintech Group"})
    assert ed.status_code == 200 and ed.json()["legal_name"] == "Acme Fintech Group"
    assert "organisation.updated" in {e["action"] for e in ed.json()["admin_events"]}

    # Suspend: live sessions end, sign-in is refused, other organisations are unaffected.
    s = client.post(f"/api/platform/organisations/{tid}/status", headers=_auth(super_admin),
                    json={"status": "SUSPENDED", "reason": "Contract paused"})
    assert s.status_code == 200 and s.json()["status"] == "SUSPENDED"
    assert client.get("/api/me", headers=_auth(tok)).status_code == 401
    assert _login(client, "ada.admin@acme-test.example", new_pw).status_code == 403
    assert client.get("/api/me", headers=H(me["actor_id"])).status_code == 403
    assert client.get("/api/me", headers=H("actor-cco")).status_code == 200
    assert client.post(f"/api/platform/organisations/{tid}/status", headers=_auth(super_admin),
                       json={"status": "SUSPENDED", "reason": "again"}).status_code == 409
    # Reactivate
    a = client.post(f"/api/platform/organisations/{tid}/status", headers=_auth(super_admin),
                    json={"status": "ACTIVE", "reason": "Contract resumed"})
    assert a.status_code == 200 and a.json()["status"] == "ACTIVE"
    assert _login(client, "ada.admin@acme-test.example", new_pw).status_code == 200
    changes = [(e["prior_state"], e["new_state"]) for e in a.json()["admin_events"] if e["action"] == "organisation.status_changed"]
    assert ("ACTIVE", "SUSPENDED") in changes and ("SUSPENDED", "ACTIVE") in changes

    # A further Organisation Admin can be appointed; it also starts with a one-time temporary password.
    ap = client.post(f"/api/platform/organisations/{tid}/admins", headers=_auth(super_admin),
                     json={"display_name": "Bo Backup", "email": "bo.backup@acme-test.example"})
    assert ap.status_code == 201 and len(ap.json()["admins"]) == 2 and ap.json()["initial_admin_password"]
