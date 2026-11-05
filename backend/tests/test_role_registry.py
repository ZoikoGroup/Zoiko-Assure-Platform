"""Stage 4: read authorization and the scoped role registry (roadmap Stage 4a/4b; audit P-01, I-10).

Requirements: ZA-IAM-001 IAM-INV-03/09, IAM-REQ-007/009/010/014/015/021/027/036/037/041, §02, §06, §08, §10, §14, §21,
§22, §23; ZA-API-001 API-REQ-008/014/031 and the existence-masking rule (404 instead of revealing a resource);
ZA-SEC-001 SEC-REQ-008/009/022; ZA-QA-001 QA-REQ-019/021 and §09 (cross-tenant ID substitution, cross-entity access).

Every refused write is checked against a fingerprint of every row in every table (tests/test_authorization.py), so
"refused" also means "changed nothing" apart from its own authorization.denied record.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, Depends, HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import select, text

from conftest import H, add_principal, stamp
from test_authorization import db_fingerprint, denials

TENANT, TENANT_B = "tenant-zoiko-group", "tenant-rr-b"
OWNER, TSA, USER, USER2, EU_READER, UK_READER = "rr-owner", "rr-tsa", "rr-user", "rr-user2", "rr-eu", "rr-uk"
B_TSA, B_USER, SUPER, COMMITTER = "rr-b-tsa", "rr-b-cco", "rr-super", "rr-commit"


@pytest.fixture(scope="module")
def principals(client):
    """A tenant with an Organisation Admin, an appointed security admin and people without roles; a second tenant with
    its own security admin and CCO; a Zoiko Super Admin. The security admin is appointed through the API."""
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        if db.get(Tenant, TENANT_B) is None:
            db.add(Tenant(tenant_id=TENANT_B, legal_name="Role Registry Probe Group", contracting_region="UK"))
            db.flush()
            db.add(LegalEntity(entity_id="entity-rr-b", tenant_id=TENANT_B, legal_name="RR Probe Ltd", country="GB",
                               home_cell_id=CELL_ID, subject_id="subj-rr-b"))
            db.add(Subject(subject_id="subj-rr-b", tenant_id=TENANT_B, entity_id="entity-rr-b",
                           subject_type="LEGAL_ENTITY", display_name="RR Probe Ltd"))
        for aid, tenant, roles in ((OWNER, TENANT, ["TENANT_OWNER"]), (TSA, TENANT, []), (USER, TENANT, []),
                                   (USER2, TENANT, []), (EU_READER, TENANT, []), (UK_READER, TENANT, []),
                                   (COMMITTER, TENANT, ["CONTROL_OWNER"]),
                                   (B_TSA, TENANT_B, ["TENANT_SECURITY_ADMIN"]), (B_USER, TENANT_B, ["CCO"]),
                                   (SUPER, None, ["SUPER_ADMIN"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    if not _assignments(TSA, role="TENANT_SECURITY_ADMIN"):
        r = assign(client, OWNER, TSA, "TENANT_SECURITY_ADMIN")
        assert r.status_code == 201, r.text
    return True


def assign(client, by, principal, role, **kw):
    body = {"principal_id": principal, "role_code": role, "reason": kw.pop("reason", "stage 4 test"), **kw}
    return client.post("/api/role-assignments", headers=H(by), json=body)


def _assignments(principal, role=None, active_only=True):
    from app.db import AdminSession
    from app.models import RoleAssignment
    with AdminSession() as db:
        q = select(RoleAssignment).where(RoleAssignment.principal_id == principal)
        if role:
            q = q.where(RoleAssignment.role_code == role)
        if active_only:
            q = q.where(RoleAssignment.status == "ACTIVE")
        return db.execute(q.order_by(RoleAssignment.record_seq)).scalars().all()


def _latest_audit(action, **where):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        q = select(AuditEvent).where(AuditEvent.action == action)
        for k, v in where.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return db.execute(q.order_by(AuditEvent.record_seq.desc())).scalars().first()


def _ids(client):
    """Identifiers of tenant A's records, one per kind of resource a GET route names in its path."""
    from app.db import AdminSession
    from app.models import EvidencePackage, Issue
    cco = H("actor-cco")
    ids = {"evidence_id": client.get("/api/evidence", headers=cco).json()[0]["evidence_id"],
           "exception_id": client.get("/api/exceptions", headers=cco).json()[0]["exception_id"],
           "comp_id": client.get("/api/compensations", headers=cco).json()[0]["compensation_id"],
           "ci_id": client.get("/api/controls", headers=cco).json()[0]["control_implementation_id"],
           "run_id": client.get("/api/ipe/source-runs", headers=cco).json()[0]["source_run_id"],
           "subject_id": "subj-entity-eu", "agent_subject_id": "subj-ai-payables"}
    obls = {o["definition"]["key"]: o for o in client.get("/api/obligations", headers=cco).json()}
    ids["lineage"] = obls["DORA_ICT_RISK_FRAMEWORK"]["lineage_id"]
    dossier = client.get(f"/api/obligations/{ids['lineage']}", headers=cco).json()
    ids["occurrence_id"] = dossier["occurrences"][0]["occurrence_id"]
    ids["evaluation_id"] = client.get("/api/applicability/evaluations", headers=cco).json()[0]["evaluation_id"]
    with AdminSession() as db:
        ids["snapshot_id"] = db.execute(text("SELECT snapshot_id FROM assurance_snapshot WHERE tenant_id = :t "
                                             "ORDER BY record_cutoff_seq, snapshot_id LIMIT 1"), {"t": TENANT}).scalar_one()
        issue = db.execute(select(Issue).where(Issue.tenant_id == TENANT)).scalars().first()
        if issue is None:
            issue = Issue(tenant_id=TENANT, title="rr probe", issue_type="PROOF_GAP", severity="LOW", priority="P3",
                          state="OPEN", source_ref_type="Probe", source_ref_id="rr", dedupe_key="rr-" + secrets.token_hex(4),
                          created_at=datetime.now(timezone.utc))
            db.add(issue)
            db.commit()
        ids["issue_id"] = issue.issue_id
        pkg = db.execute(select(EvidencePackage).where(EvidencePackage.tenant_id == TENANT)).scalars().first()
    if pkg is None:
        r = client.post("/api/packages", headers=H("actor-audit"), json={
            "entity_id": "entity-eu", "period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z",
            "obligation_lineage_ids": [ids["lineage"]]})
        assert r.status_code == 200, r.text
        ids["package_id"] = r.json()["package_id"]
    else:
        ids["package_id"] = pkg.package_id
    conflict = client.post("/api/evidence-conflicts", headers=H("actor-owner"),
                           json={"member_evidence_ids": [ids["evidence_id"]], "description": "stage 4 read probe"})
    assert conflict.status_code == 200, conflict.text
    ids["conflict_id"] = conflict.json()["conflict_id"]
    ids["assignment_id"] = _assignments(TSA, role="TENANT_SECURITY_ADMIN")[0].assignment_id
    ids["principal_id"] = USER
    return ids


# ------------------------------------------------------------------------------------------------ 4a operation registry
def test_every_operation_declares_its_authorization_or_is_explicitly_public(client):
    """API-REQ-031 / IAM-REQ-007: every route of every method has a stable operationId and a declared requirement; the
    exceptions are an explicit, reasoned public list. The OpenAPI document uses the same operationIds."""
    from app.main import ROUTERS, app
    from app.security import PUBLIC_MUTATIONS, PUBLIC_READS, operations, unguarded_operations
    ops = operations(ROUTERS)
    assert unguarded_operations(ROUTERS) == []
    assert len(ops) >= 140 and len({o.operation_id for o in ops}) == len(ops)
    assert set(PUBLIC_READS) == {"/api/health", "/api/dictionary", "/api/trust/public-key", "/api/auth/session",
                                 "/api/auth/demo-accounts"}
    public = {(o.method, o.path) for o in ops if o.public}
    assert public == {("GET", p) for p in PUBLIC_READS} | {("POST", p) for p in PUBLIC_MUTATIONS}
    assert all(o.requirement is not None for o in ops if o.method == "GET" and (o.method, o.path) not in public)
    spec = app.openapi()
    assert {op["operationId"] for path in spec["paths"].values() for op in path.values()} == {o.operation_id for o in ops}


def test_an_unguarded_read_is_reported_deny_by_default():
    from app.security import Ctx, authorize, get_ctx, unguarded_operations
    probe = APIRouter()

    @probe.get("/probe/unguarded-read")
    def unguarded_read(ctx: Ctx = Depends(get_ctx)):  # authenticated, but no permission declared
        return {}

    @probe.get("/probe/guarded-read")
    def guarded_read(ctx: Ctx = Depends(authorize("read"))):
        return {}
    assert unguarded_operations([probe]) == ["GET /api/probe/unguarded-read"]


# ------------------------------------------------------------------------------------------------ 4b role registry
def test_role_assignment_lifecycle_is_recorded_and_takes_effect_immediately(client, principals):
    from app.db import AdminSession
    from app.models import Actor, RoleAssignmentEvent
    assert client.get("/api/exceptions", headers=H(USER)).status_code == 403  # no role: no read
    r = assign(client, TSA, USER, "CCO", reason="Quarterly compliance cover")
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["role_code"], a["scope_type"], a["scope_id"], a["status"], a["source"], a["granted_by"], a["in_force"]) == (
        "CCO", "TENANT", TENANT, "ACTIVE", "ADMIN", TSA, True)
    assert client.get("/api/exceptions", headers=H(USER)).status_code == 200  # IAM-REQ-015: next decision
    assert assign(client, TSA, USER, "CCO").status_code == 409  # one assignment per role, scope and period
    me = client.get("/api/me", headers=H(USER)).json()
    assert me["roles"] == ["CCO"] and "attest" in me["permissions"]
    tsa_grant = _assignments(TSA, role="TENANT_SECURITY_ADMIN")[0].assignment_id
    with AdminSession() as db:
        assert db.get(Actor, USER).roles == ["CCO"]  # the org_actor.roles cache follows the registry
        ev = db.execute(select(RoleAssignmentEvent).where(RoleAssignmentEvent.assignment_id == a["assignment_id"])).scalars().one()
        assert (ev.event_type, ev.actor_id, ev.authorized_by, ev.tenant_id) == ("ASSIGNED", TSA, [tsa_grant], TENANT)
    au = _latest_audit("role.assigned", object_id=a["assignment_id"])
    assert (au.actor_id, au.tenant_id, au.new_state) == (TSA, TENANT, "CCO@TENANT")
    assert tsa_grant in au.reason and "policy za-authz-" in au.reason and "Quarterly compliance cover" in au.reason

    r = client.post(f"/api/role-assignments/{a['assignment_id']}/revoke", headers=H(TSA), json={"reason": "Cover ended"})
    assert r.status_code == 200 and r.json()["status"] == "REVOKED" and r.json()["in_force"] is False
    assert client.get("/api/exceptions", headers=H(USER)).status_code == 403  # revoked: refused at once
    assert client.post(f"/api/role-assignments/{a['assignment_id']}/revoke", headers=H(TSA),
                       json={"reason": "again"}).status_code == 409
    history = client.get(f"/api/role-assignments/{a['assignment_id']}/events", headers=H(TSA)).json()
    assert [e["event_type"] for e in history] == ["ASSIGNED", "REVOKED"]
    assert history[0]["record_seq"] < history[1]["record_seq"]
    au = _latest_audit("role.revoked", object_id=a["assignment_id"])
    assert (au.actor_id, au.prior_state, au.new_state) == (TSA, "ACTIVE", "REVOKED") and "Cover ended" in au.reason
    with AdminSession() as db:
        assert db.get(Actor, USER).roles == []
    closed = client.get("/api/role-assignments", params={"principal_id": USER, "include_closed": True},
                        headers=H(TSA)).json()
    assert [(x["role_code"], x["status"]) for x in closed] == [("CCO", "REVOKED")]
    assert client.get("/api/role-assignments", params={"principal_id": USER}, headers=H(TSA)).json() == []


def test_validity_windows_bound_authority(client, principals):
    from app.db import AdminSession
    from app.models import RoleAssignment
    from app.platform import next_record_seq
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    r = assign(client, TSA, USER2, "RISK_APPROVER", valid_from=start.isoformat(),
               valid_to=(start + timedelta(days=1)).isoformat())
    assert r.status_code == 201 and r.json()["in_force"] is False
    assert client.get("/api/exceptions", headers=H(USER2)).status_code == 403  # not yet in force
    with AdminSession() as db:  # an assignment whose period has already ended (valid_from can never be rewritten)
        now = datetime.now(timezone.utc)
        db.add(RoleAssignment(tenant_id=TENANT, principal_id=USER2, role_code="INTERNAL_AUDIT", scope_type="TENANT",
                              scope_id=TENANT, valid_from=now - timedelta(days=2), valid_to=now - timedelta(days=1),
                              status="ACTIVE", source="TEST", reason="expired probe", record_seq=next_record_seq(db)))
        db.commit()
    assert client.get("/api/exceptions", headers=H(USER2)).status_code == 403  # expired
    access = client.get(f"/api/principals/{USER2}/effective-access", headers=H(TSA)).json()
    assert access["assignments"] == [] and access["tenant_permissions"] == []


def test_escalation_and_self_assignment_are_refused_and_change_nothing(client, principals):
    """IAM-REQ-021 (no self-elevation), IAM §08 (who manages roles), IAM §14/§15 (auditor and workload roles are not
    workforce roles), IAM-REQ-002 (principal classes), API-REQ-014 (no mass assignment), no back-dated authority."""
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    cases = [  # (actor, principal, role, extra body, status, recorded reason or None)
        (TSA, TSA, "CCO", {}, 403, "SELF_ASSIGNMENT"),
        (OWNER, OWNER, "TENANT_SECURITY_ADMIN", {}, 403, "SELF_ASSIGNMENT"),
        (TSA, USER2, "TENANT_SECURITY_ADMIN", {}, 403, "NOT_DELEGABLE_BY_ACTOR"),
        (OWNER, USER2, "CCO", {}, 403, "NOT_DELEGABLE_BY_ACTOR"),
        (TSA, USER2, "TENANT_OWNER", {}, 403, "ROLE_NOT_ASSIGNABLE"),
        (TSA, USER2, "SUPER_ADMIN", {}, 403, "ROLE_NOT_ASSIGNABLE"),
        (TSA, USER2, "CONTENT_RELEASE_MANAGER", {}, 403, "ROLE_NOT_ASSIGNABLE"),
        (TSA, USER2, "CONNECTOR_CERTIFICATION_MANAGER", {}, 403, "ROLE_NOT_ASSIGNABLE"),
        (TSA, "actor-ext-auditor", "EXTERNAL_AUDITOR", {}, 403, "ROLE_NOT_ASSIGNABLE"),
        (TSA, "actor-svc", "SERVICE", {}, 403, "ROLE_NOT_ASSIGNABLE"),
        ("actor-cco", USER2, "CCO", {}, 403, "MISSING_PERMISSION"),
        ("actor-admin", USER2, "CCO", {}, 403, "MISSING_PERMISSION"),  # PLATFORM_ADMIN's manage_tenant is not enough
        ("actor-audit", USER2, "CCO", {}, 403, "MISSING_PERMISSION"),
        ("actor-ext-auditor", USER2, "CCO", {}, 403, "HUMAN_REQUIRED"),  # role administration is a HUMAN action
        ("actor-svc", USER2, "CCO", {}, 403, "HUMAN_REQUIRED"),
        ("actor-agent", USER2, "CCO", {}, 403, "AGENT_WITHOUT_GRANT"),
        (SUPER, USER2, "CCO", {}, 403, "MISSING_PERMISSION"),
        (TSA, USER2, "GOD_MODE", {}, 422, None),
        (TSA, "actor-svc", "CCO", {}, 422, None),          # a workload cannot hold a human role
        (TSA, "actor-agent", "CCO", {}, 422, None),
        (TSA, "actor-ext-auditor", "CCO", {}, 422, None),  # an external auditor is not workforce (IAM §14)
        (TSA, USER2, "CCO", {"scope_type": "PLATFORM"}, 422, None),
        (TSA, USER2, "CCO", {"tenant_id": TENANT_B}, 422, None),  # server-owned field
        (TSA, USER2, "CCO", {"granted_by": OWNER}, 422, None),
        (TSA, USER2, "CCO", {"valid_from": past}, 422, None),
        (TSA, USER2, "CCO", {"valid_from": "2027-01-02T00:00:00Z", "valid_to": "2027-01-01T00:00:00Z"}, 422, None),
        (TSA, USER2, "CCO", {"scope_id": TENANT_B}, 422, None),
        (TSA, USER2, "CCO", {"scope_type": "LEGAL_ENTITY"}, 404, None),  # no entity named
        (TSA, "no-such-principal", "CCO", {}, 404, None),
    ]
    before = db_fingerprint()
    wrong = []
    for actor, principal, role, extra, status, reason in cases:
        n = denials(actor)
        r = assign(client, actor, principal, role, **extra)
        if r.status_code != status:
            wrong.append((actor, principal, role, extra, r.status_code, r.text[:200]))
            continue
        if reason:
            ev = _latest_audit("authorization.denied", actor_id=actor)
            if denials(actor) != n + 1 or ev.new_state != reason:
                wrong.append((actor, principal, role, "recorded", ev.new_state if ev else None))
    assert not wrong, wrong
    assert db_fingerprint() == before
    # Revocation needs the same authority as assignment: a CCO cannot remove the security admin, nor the TSA itself.
    tsa_grant = _assignments(TSA, role="TENANT_SECURITY_ADMIN")[0].assignment_id
    for actor, status in (("actor-cco", 403), (TSA, 403), ("actor-admin", 403)):
        assert client.post(f"/api/role-assignments/{tsa_grant}/revoke", headers=H(actor),
                           json={"reason": "probe"}).status_code == status, actor
    owner_grant = _assignments(OWNER, role="TENANT_OWNER")[0].assignment_id
    assert client.post(f"/api/role-assignments/{owner_grant}/revoke", headers=H(TSA),
                       json={"reason": "probe"}).status_code == 403  # TENANT_OWNER belongs to the platform path
    assert db_fingerprint() == before


def test_role_registry_is_reachable_only_by_role_administrators(client, principals):
    before = db_fingerprint()
    for actor in ("actor-cco", "actor-audit", "actor-admin", "actor-ext-auditor", "actor-svc", "actor-agent", SUPER,
                  "actor-content-analyst"):
        for path in ("/api/roles", "/api/role-assignments", f"/api/principals/{USER}/effective-access"):
            assert client.get(path, headers=H(actor)).status_code == 403, (actor, path)
    assert db_fingerprint() == before
    for actor in (TSA, OWNER):
        cat = client.get("/api/roles", headers=H(actor)).json()
        assert cat["policy_version"].startswith("za-authz-")
        rules = {r["role_code"]: r for r in cat["roles"]}
        assert rules["TENANT_SECURITY_ADMIN"]["assigned_by"] == "permission delegate_admin"
        assert rules["CCO"]["assigned_by"] == "permission manage_role_assignments"
        assert rules["TENANT_OWNER"]["assignable"] is False and rules["EXTERNAL_AUDITOR"]["assignable"] is False
        assert not {"SUPER_ADMIN", "CONTENT_ANALYST", "CONNECTOR_CERTIFICATION_MANAGER"} & set(rules)


def test_role_administration_never_crosses_the_tenant_boundary(client, principals):
    """IAM-INV-05, IAM-REQ-009, TEN isolation: another organisation's principals, entities and assignments answer 404
    exactly like ones that do not exist, and nothing is written."""
    ids = {"assignment": _assignments(TSA, role="TENANT_SECURITY_ADMIN")[0].assignment_id}
    before = db_fingerprint()
    assert assign(client, B_TSA, USER, "CCO").status_code == 404
    assert assign(client, B_TSA, "rr-b-cco", "CONTROL_OWNER", scope_type="LEGAL_ENTITY",
                  scope_id="entity-eu").status_code == 404  # an entity of tenant A
    assert client.post(f"/api/role-assignments/{ids['assignment']}/revoke", headers=H(B_TSA),
                       json={"reason": "probe"}).status_code == 404
    assert client.get(f"/api/role-assignments/{ids['assignment']}/events", headers=H(B_TSA)).status_code == 404
    assert client.get(f"/api/principals/{USER}/effective-access", headers=H(B_TSA)).status_code == 404
    assert db_fingerprint() == before
    listed = client.get("/api/role-assignments", params={"include_closed": True}, headers=H(B_TSA)).json()
    assert listed and {a["tenant_id"] for a in listed} == {TENANT_B}
    # An assignment recorded under another tenant would never confer authority there (defence in depth below RLS).
    from app.db import AdminSession
    from app.iam import active_assignments
    from app.models import Actor, RoleAssignment
    with AdminSession() as db:
        planted = RoleAssignment(tenant_id=TENANT, principal_id=B_USER, role_code="CCO", scope_type="TENANT",
                                 scope_id=TENANT, valid_from=datetime.now(timezone.utc), status="ACTIVE", source="TEST",
                                 record_seq=0)
        db.add(planted)
        db.flush()
        assert planted.assignment_id not in {a.assignment_id for a in active_assignments(db, db.get(Actor, B_USER))}
        db.rollback()


# ------------------------------------------------------------------------------------------------ resource scope
def test_entity_scoped_roles_reach_their_entity_only(client, principals):
    """IAM-REQ-010 / QA §09 cross-entity access: an entity-scoped role confers its permissions only on resources the
    server resolves to that entity; anything else is answered like a missing record (404) and the refusal is recorded;
    tenant-wide lists are refused; entity scope never extends to state-changing routes in this stage (fail closed)."""
    ids = _ids(client)
    for principal, entity in ((EU_READER, "entity-eu"), (UK_READER, "entity-uk")):
        if not _assignments(principal, role="INTERNAL_AUDIT"):
            r = assign(client, TSA, principal, "INTERNAL_AUDIT", scope_type="LEGAL_ENTITY", scope_id=entity)
            assert r.status_code == 201 and r.json()["scope_id"] == entity, r.text
    eid = ids["evidence_id"]  # seeded evidence belongs to entity-eu
    for path in (f"/api/evidence/{eid}", f"/api/evidence/{eid}/download", f"/api/evidence/{eid}/holds/events",
                 "/api/subjects/subj-entity-eu/facts"):
        assert client.get(path, headers=H(EU_READER)).status_code == 200, path
    assert client.post(f"/api/evidence/{eid}/verify", headers=H(EU_READER)).status_code == 200  # read-class check
    missing = client.get("/api/evidence/no-such-evidence", headers=H(UK_READER))
    n = denials(UK_READER)
    other = client.get(f"/api/evidence/{eid}", headers=H(UK_READER))
    assert (other.status_code, other.json()) == (missing.status_code, missing.json()) == (404, {"detail": "evidence not found"})
    assert denials(UK_READER) == n + 1
    assert _latest_audit("authorization.denied", actor_id=UK_READER).new_state == "ENTITY_OUT_OF_SCOPE"
    assert client.get("/api/subjects/subj-entity-uk/facts", headers=H(UK_READER)).status_code == 200
    assert client.get("/api/subjects/subj-entity-eu/facts", headers=H(UK_READER)).status_code == 404
    for path in ("/api/evidence", "/api/exceptions", "/api/obligations", "/api/audit"):  # tenant-wide lists
        assert client.get(path, headers=H(EU_READER)).status_code == 403, path
    before = db_fingerprint()
    assert client.post("/api/packages", headers=H(EU_READER), json={
        "entity_id": "entity-eu", "period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z",
        "obligation_lineage_ids": [ids["lineage"]]}).status_code == 403
    assert db_fingerprint() == before
    me = client.get("/api/me", headers=H(EU_READER)).json()
    assert me["permissions"] == [] and [(a["role_code"], a["scope_type"], a["scope_id"]) for a in me["role_assignments"]] == [
        ("INTERNAL_AUDIT", "LEGAL_ENTITY", "entity-eu")]
    access = client.get(f"/api/principals/{EU_READER}/effective-access", headers=H(TSA)).json()
    assert access["tenant_permissions"] == [] and "read" in access["entity_permissions"]["entity-eu"]


# ------------------------------------------------------------------------------------------------ read authorization
AUDITOR_ALLOWED = ["/api/evidence", "/api/evidence/{evidence_id}", "/api/evidence/{evidence_id}/download",
                   "/api/packages", "/api/ledger", "/api/controls", "/api/controls/{ci_id}", "/api/obligations",
                   "/api/obligations/{lineage}", "/api/snapshots/{snapshot_id}", "/api/exceptions",
                   "/api/exceptions/{exception_id}/events", "/api/compensations", "/api/issues",
                   "/api/applicability/determinations", "/api/applicability/evaluations/{evaluation_id}",
                   "/api/dashboard", "/api/catalog/obligation-definitions", "/api/rules/versions", "/api/ipe/source-runs"]
AUDITOR_DENIED = ["/api/audit", "/api/outbox", "/api/actors", "/api/org/tenant", "/api/org/subjects",
                  "/api/subjects/{subject_id}/facts", "/api/subjects/{subject_id}/facts/history", "/api/fact-requests",
                  "/api/rules/drafts", "/api/ai/assets", "/api/ai/policy-bundle/{agent_subject_id}", "/api/connectors",
                  "/api/roles", "/api/role-assignments"]


def _fill(path, ids):
    return re.sub(r"\{([^}]+)\}", lambda m: ids[m.group(1)], path)


def test_external_auditor_reads_only_the_engagement_allowlist(client, principals):
    """IAM §08/§14, IAM-REQ-027: selected evidence, controls/obligation dossiers, packages and reports; nothing of the
    organisation's administration, people, facts, drafts or AI estate; no mutation (tests/test_authorization.py)."""
    ids = _ids(client)
    aud = H("actor-ext-auditor")
    for path in AUDITOR_ALLOWED:
        assert client.get(_fill(path, ids), headers=aud).status_code == 200, path
    n = denials("actor-ext-auditor")
    for path in AUDITOR_DENIED:
        assert client.get(_fill(path, ids), headers=aud).status_code == 403, path
    assert denials("actor-ext-auditor") == n + len(AUDITOR_DENIED)
    assert client.post(f"/api/packages/{ids['package_id']}/verify", headers=aud).status_code == 200  # verification
    assert client.get("/api/me", headers=aud).json()["permissions"] == ["audit_read", "verify"]


def test_read_permissions_are_distinct_from_mutation_permissions(client, principals):
    """A role administrator administers access, not the record; a workforce reader cannot write."""
    ids = _ids(client)
    before = db_fingerprint()
    for path in ("/api/evidence", f"/api/evidence/{ids['evidence_id']}", "/api/exceptions", "/api/audit", "/api/dashboard"):
        assert client.get(path, headers=H(TSA)).status_code == 403, path
    for method, path, body in (("POST", f"/api/evidence/{ids['evidence_id']}/invalidate", {"reason": "probe"}),
                               ("POST", f"/api/issues/{ids['issue_id']}/transition", {"state": "TRIAGED"}),
                               ("POST", "/api/role-assignments", {"principal_id": USER2, "role_code": "CCO",
                                                                  "reason": "probe"})):
        for actor in ("actor-audit", "actor-ext-auditor"):
            assert client.request(method, path, headers=H(actor), json=body).status_code == 403, (actor, path)
    assert db_fingerprint() == before


HISTORY = ["/api/exceptions/{exception_id}/events", "/api/compensations/{comp_id}/events", "/api/issues/{issue_id}/events",
           "/api/occurrences/{occurrence_id}/events", "/api/evidence/{evidence_id}/holds/events",
           "/api/evidence-conflicts/{conflict_id}/events"]


def test_governance_history_endpoints_enforce_read_authorization(client, principals):
    """Stage 3c history routes under Stage 4 read authorization: tenant readers and the auditor allowlist read them;
    another organisation gets 404 (existence masked); platform staff, agents and role administrators get 403;
    no session gets 401. Nothing is written by any refusal."""
    ids = _ids(client)
    before = db_fingerprint()
    for tmpl in HISTORY:
        path = _fill(tmpl, ids)
        for actor in ("actor-cco", "actor-audit", "actor-ext-auditor"):
            r = client.get(path, headers=H(actor))
            assert r.status_code == 200 and isinstance(r.json(), list), (actor, path)
        assert client.get(path, headers=H(B_USER)).status_code == 404, path
        for actor in (SUPER, "actor-content-analyst", "actor-agent", TSA):
            assert client.get(path, headers=H(actor)).status_code == 403, (actor, path)
        assert client.get(path).status_code == 401, path
    assert db_fingerprint() == before


def test_cross_tenant_reads_fail_on_every_identifier_route(client, principals):
    """QA-REQ-019 / QA §09 "cross-tenant ID substitution" (generated over every GET route that names a tenant record
    in its path): another organisation's CCO never receives a tenant-A record, and nothing is written."""
    from app.main import ROUTERS
    from app.security import operations
    ids = _ids(client)
    # Zoiko content / platform registry, not tenant data (source_id, odef_id: global catalogue records, 4 Nov 2026)
    global_params = {"rv_id", "draft_id", "release_id", "tenant_id", "source_id", "odef_id"}
    before, checked, leaked = db_fingerprint(), 0, []
    for op in operations(ROUTERS):
        params = set(re.findall(r"\{([^}]+)\}", op.path))
        if op.method != "GET" or not params or params & global_params:
            continue
        r = client.get(_fill(op.path, ids), headers=H(B_USER))
        checked += 1
        if r.status_code not in (403, 404):
            leaked.append((op.path, r.status_code))
    r = client.get("/api/history/compare", headers=H(B_USER), params={
        "subject_id": ids["subject_id"], "obligation_definition_id": "x", "valid_at": "2026-07-01T00:00:00Z",
        "left_seq": 1, "right_seq": 2})
    assert r.status_code == 404
    assert checked >= 20 and not leaked, leaked
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ Super Admin
def test_super_admin_has_no_tenant_authority_and_appointment_is_recorded(client, principals):
    from app.db import AdminSession
    from app.models import RoleAssignment
    me = client.get("/api/me", headers=H(SUPER)).json()
    assert [(a["role_code"], a["scope_type"], a["tenant_id"]) for a in me["role_assignments"]] == [("SUPER_ADMIN", "PLATFORM", None)]
    for path in ("/api/evidence", "/api/audit", "/api/obligations", "/api/catalog/sources", "/api/dashboard"):
        assert client.get(path, headers=H(SUPER)).status_code == 403, path
    r = client.post(f"/api/platform/organisations/{TENANT_B}/admins", headers=H(SUPER),
                    json={"display_name": "RR B Admin", "email": f"rr-b-admin-{secrets.token_hex(3)}@test.example"})
    assert r.status_code == 201, r.text
    new_admin = next(a for a in r.json()["admins"] if a["display_name"] == "RR B Admin")["actor_id"]
    with AdminSession() as db:
        rows = db.execute(select(RoleAssignment).where(RoleAssignment.principal_id == new_admin)).scalars().all()
        assert [(a.role_code, a.scope_type, a.tenant_id, a.source, a.granted_by) for a in rows] == [
            ("TENANT_OWNER", "TENANT", TENANT_B, "PLATFORM_APPOINTMENT", SUPER)]
        mine = db.execute(select(RoleAssignment).where(RoleAssignment.principal_id == SUPER)).scalars().all()
        assert [(a.role_code, a.tenant_id) for a in mine] == [("SUPER_ADMIN", None)]  # gained nothing in the tenant
    au = _latest_audit("role.assigned", object_id=rows[0].assignment_id)
    assert (au.actor_id, au.tenant_id) == (SUPER, TENANT_B)


# ------------------------------------------------------------------------------------------------ commit-time authority
def test_a_mutation_does_not_commit_after_its_authorising_role_is_revoked(pg, principals):
    """IAM-REQ-014 / IAM-REQ-036: the authority is re-read immediately before commit, not frozen at the guard.
    PostgreSQL-only: it needs a second writer and read-committed visibility, which SQLite cannot provide."""
    from app.db import AdminSession, SessionLocal, set_principal
    from app.models import Actor, AuditEvent, RoleAssignment
    from app.platform import audit
    from app.security import Ctx, Requirement
    grant = _assignments(COMMITTER, role="CONTROL_OWNER")[0].assignment_id
    db = SessionLocal()
    try:
        with AdminSession() as adm:
            actor = adm.get(Actor, COMMITTER)
        set_principal(db, TENANT, COMMITTER)
        ctx = Ctx(db=db, actor=actor, route="POST /probe")
        ctx.check(Requirement(("submit_evidence",)))
        assert ctx.authority == (grant,)
        db.info["authority"] = ctx.authority
        audit(db, action="rr.probe_write", object_type="Probe", object_id="rr-commit", actor=actor, tenant_id=TENANT)
        db.flush()
        with AdminSession() as adm:  # the revocation commits first
            row = adm.get(RoleAssignment, grant)
            row.status, row.revoked_at, row.valid_to = "REVOKED", datetime.now(timezone.utc), datetime.now(timezone.utc)
            adm.commit()
        with pytest.raises(HTTPException) as e:
            db.commit()
        assert e.value.status_code == 403
        db.rollback()
    finally:
        db.close()
    with AdminSession() as adm:
        assert adm.execute(select(AuditEvent).where(AuditEvent.action == "rr.probe_write")).first() is None


# ------------------------------------------------------------------------------------------------ PostgreSQL below the API
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return engine


def test_role_registry_rows_are_tenant_isolated_and_closure_only_in_postgresql(pg, principals):
    """FORCE RLS on both registry tables (IAM-REQ-041, DAT-REQ-003): no context sees nothing; a tenant sees its own rows
    only; Zoiko platform rows need a verified platform principal and can never be written by the runtime role; the
    runtime role can change only the closure columns, never DELETE, and never touch history."""
    from sqlalchemy.exc import DBAPIError, ProgrammingError
    insert = text("INSERT INTO iam_role_assignment (assignment_id, tenant_id, principal_id, role_code, scope_type, "
                  "scope_id, valid_from, status, source, record_seq, recorded_at) VALUES (:i, :t, 'p', 'CCO', :st, :t, "
                  "now(), 'ACTIVE', 'PROBE', 0, now())")
    with pg.connect() as c:
        tx = c.begin()
        for t in ("iam_role_assignment", "iam_role_assignment_event"):
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0, t  # no verified context
        stamp(c, TENANT, actor=TSA)
        tenants = set(c.execute(text("SELECT DISTINCT tenant_id FROM iam_role_assignment")).scalars())
        assert tenants == {TENANT}
        assert set(c.execute(text("SELECT DISTINCT tenant_id FROM iam_role_assignment_event")).scalars()) == {TENANT}
        for sql, params, match in (
                (insert, {"i": "rr-pg-1", "t": None, "st": "PLATFORM"}, "row-level security"),  # platform row
                (insert, {"i": "rr-pg-2", "t": TENANT_B, "st": "TENANT"}, "row-level security"),  # other tenant
                (text("UPDATE iam_role_assignment SET role_code = 'SUPER_ADMIN' WHERE tenant_id = :t"), {"t": TENANT},
                 "permission denied"),
                (text("DELETE FROM iam_role_assignment WHERE tenant_id = :t"), {"t": TENANT}, "permission denied"),
                (text("UPDATE iam_role_assignment_event SET role_code = 'X'"), {}, "permission denied"),
                (text("DELETE FROM iam_role_assignment_event"), {}, "permission denied")):
            sp = c.begin_nested()
            with pytest.raises((ProgrammingError, DBAPIError), match=match):
                c.execute(sql, params)
            sp.rollback()
        stamp(c, TENANT_B, actor=B_TSA)
        assert set(c.execute(text("SELECT DISTINCT tenant_id FROM iam_role_assignment")).scalars()) == {TENANT_B}
        stamp(c, None, actor=SUPER)  # verified platform principal: platform rows only
        assert set(c.execute(text("SELECT DISTINCT tenant_id FROM iam_role_assignment")).scalars()) == {None}
        sp = c.begin_nested()
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(insert, {"i": "rr-pg-3", "t": None, "st": "PLATFORM"})
        sp.rollback()
        tx.rollback()


@pytest.fixture
def schema(pg):
    from app.db import admin_engine
    name = "za_rr_" + secrets.token_hex(4)
    with admin_engine.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    yield name
    with admin_engine.begin() as c:
        c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')  # this test's throwaway schema only


def test_migration_0009_backfills_roles_without_inventing_history(pg, schema):
    """DAT-REQ-024: every org_actor.roles entry becomes an ACTIVE, explicitly scoped assignment valid from the migration
    (never earlier), with its own record sequence and an ASSIGNED event attributed to the migration; downgrading leaves
    org_actor.roles as it was."""
    from app import migrate
    from app.db import CELL_ID, admin_engine
    migrate.upgrade("0008", schema=schema, provision_role=False)
    with admin_engine.begin() as c:
        c.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        for aid, tenant, roles in (("m-a", "t-m", '["CCO", "RISK_APPROVER"]'), ("m-b", "t-m", "[]"),
                                   ("m-c", None, '["SUPER_ADMIN"]'), ("m-d", "t-m", '"not-a-list"')):
            c.execute(text("INSERT INTO org_actor (actor_id, tenant_id, actor_type, display_name, roles) "
                           "VALUES (:a, :t, 'HUMAN', :a, CAST(:r AS json))"), {"a": aid, "t": tenant, "r": roles})
        c.execute(text("INSERT INTO platform_record_sequence (cell_id, current_seq) VALUES (:c, 100)"), {"c": CELL_ID})
        started = c.execute(text("SELECT now()")).scalar()
    migrate.upgrade(schema=schema, provision_role=False)
    with admin_engine.begin() as c:
        c.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        rows = c.execute(text("SELECT principal_id, tenant_id, role_code, scope_type, scope_id, status, source, "
                              "granted_by, valid_from, valid_to, record_seq FROM iam_role_assignment "
                              "ORDER BY record_seq")).all()
        assert [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[9]) for r in rows] == [
            ("m-c", None, "SUPER_ADMIN", "PLATFORM", None, "ACTIVE", "MIGRATION", None, None),
            ("m-a", "t-m", "CCO", "TENANT", "t-m", "ACTIVE", "MIGRATION", None, None),
            ("m-a", "t-m", "RISK_APPROVER", "TENANT", "t-m", "ACTIVE", "MIGRATION", None, None)]
        assert [r[10] for r in rows] == [101, 102, 103] and all(r[8] >= started for r in rows)
        assert c.execute(text("SELECT current_seq FROM platform_record_sequence WHERE cell_id = :c"),
                         {"c": CELL_ID}).scalar() == 103
        events = c.execute(text("SELECT event_type, actor_id, record_seq FROM iam_role_assignment_event "
                                "ORDER BY record_seq")).all()
        assert events == [("ASSIGNED", "system:migration:0009", s) for s in (101, 102, 103)]
    st = migrate.downgrade("0008", confirm=True, schema=schema)
    assert st.revision == "0008"
    with admin_engine.begin() as c:
        c.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        assert c.execute(text("SELECT to_regclass('iam_role_assignment')")).scalar() is None
        assert c.execute(text("SELECT roles::text FROM org_actor WHERE actor_id = 'm-a'")).scalar() == '["CCO", "RISK_APPROVER"]'
