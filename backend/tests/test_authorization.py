"""P0 authorization and integrity remediation (Volume 01 audit findings F2, F1, F6).

F2  Every state-changing route is behind the deny-by-default guard (app/security.py `authorize`). Read-only roles,
    agents and other organisations are refused before anything is written: each refused batch is checked against a
    fingerprint of every row in every table.
F1  A source run's IPE result is the one under the policy it was executed with. Re-evaluation needs an authorised
    human, cannot switch to another (weaker) policy, and never replaces the bound result that assurance uses.
F6  The connector certification registry is shared by every organisation: only Zoiko platform staff holding
    certify_connectors may change it, through the route or the service function, and every change is audited.
"""
import hashlib
import re

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import delete, select, text

from conftest import H, add_principal

TENANT_B = "tenant-authz-b"
PROBE_ID = "authz-probe-id"


@pytest.fixture(scope="module")
def actors(client):
    """Extra principals: an Organisation Admin, Zoiko platform staff, and a second organisation with its own roles."""
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        if db.get(Tenant, TENANT_B) is None:
            db.add(Tenant(tenant_id=TENANT_B, legal_name="Authz Probe Group", contracting_region="UK"))
            db.flush()
            db.add(LegalEntity(entity_id="entity-authz-b", tenant_id=TENANT_B, legal_name="Authz Probe Ltd", country="GB",
                               home_cell_id=CELL_ID, subject_id="subj-authz-b"))
            db.add(Subject(subject_id="subj-authz-b", tenant_id=TENANT_B, entity_id="entity-authz-b",
                           subject_type="LEGAL_ENTITY", display_name="Authz Probe Ltd"))
            for aid, tenant, atype, roles in (
                    ("authz-owner", "tenant-zoiko-group", "HUMAN", ["TENANT_OWNER"]),
                    ("authz-super", None, "HUMAN", ["SUPER_ADMIN"]),
                    ("authz-certifier", None, "HUMAN", ["CONNECTOR_CERTIFICATION_MANAGER"]),
                    ("authz-certifier-svc", None, "SERVICE", ["CONNECTOR_CERTIFICATION_MANAGER"]),
                    ("authz-b-cco", TENANT_B, "HUMAN", ["CCO"]),
                    ("authz-b-owner", TENANT_B, "HUMAN", ["CONTROL_OWNER"]),
                    ("authz-b-audit", TENANT_B, "HUMAN", ["INTERNAL_AUDIT"]),
                    ("authz-b-admin", TENANT_B, "HUMAN", ["PLATFORM_ADMIN"])):
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type=atype, display_name=aid, roles=roles)
            db.commit()
    return True


DENIED = "authorization.denied"


def db_fingerprint() -> str:
    """Every row of every table. A refused request must leave this unchanged: no write, no other audit row, no outbox
    event. The one record a refusal does write is its own `authorization.denied` audit row (ZA-IAM-001 §21, Task 2):
    those rows are left out, and the record sequence is hashed net of the one number each of them consumed, so any
    other write, including any other use of the sequence, still changes the fingerprint. Tests check the denial rows
    themselves with denials()."""
    from app.db import AdminSession, Base
    h = hashlib.sha256()
    with AdminSession() as db:
        n_denied = denials(db=db)
        for table in Base.metadata.sorted_tables:
            h.update(table.name.encode())
            rows = []
            for r in db.execute(select(table)):
                m = dict(r._mapping)
                if table.name == "platform_audit_event" and m["action"] == DENIED:
                    continue
                if table.name == "platform_record_sequence":
                    m["current_seq"] -= n_denied
                rows.append(repr(tuple(m.values())))
            for row in sorted(rows):
                h.update(row.encode())
    return h.hexdigest()


def denials(actor: str | None = None, db=None) -> int:
    """How many refusals are recorded (for `actor`, if given)."""
    from sqlalchemy import func
    from app.db import AdminSession
    from app.models import AuditEvent
    q = select(func.count()).select_from(AuditEvent).where(AuditEvent.action == DENIED)
    if actor:
        q = q.where(AuditEvent.actor_id == actor)
    if db is not None:
        return db.execute(q).scalar()
    with AdminSession() as s:
        return s.execute(q).scalar()


def mutating_routes() -> list[tuple[str, str]]:
    """(method, path template) for every guarded state-changing route, read from the live routers."""
    from app.main import ROUTERS
    from app.security import MUTATING_METHODS, PUBLIC_MUTATIONS
    out = []
    for router in ROUTERS:
        for route in router.routes:
            if isinstance(route, APIRoute) and route.methods & MUTATING_METHODS and "/api" + route.path not in PUBLIC_MUTATIONS:
                out += [(m, "/api" + route.path) for m in sorted(route.methods & MUTATING_METHODS)]
    return out


def call(client, method, path, actor, json=None):
    return client.request(method, re.sub(r"\{[^}]+\}", PROBE_ID, path), headers=H(actor), json={} if json is None else json)


# ------------------------------------------------------------------------------------------------ F2: the guard
READ_ONLY_CHECKS = {("POST", "/api/obligations/{lineage}/preview"), ("POST", "/api/evidence/{evidence_id}/verify"),
                    ("POST", "/api/ledger/verify"), ("POST", "/api/packages/{package_id}/verify")}
CONTENT_AUTHOR_ROUTES = {
    ("POST", "/api/catalog/sources"), ("POST", "/api/catalog/sources/{source_id}/extract"),
    ("PUT", "/api/catalog/provisions/{provision_id}"), ("POST", "/api/rules/drafts"), ("PUT", "/api/rules/drafts/{draft_id}"),
    ("POST", "/api/rules/drafts/{draft_id}/validate"), ("POST", "/api/rules/drafts/{draft_id}/test"),
    ("POST", "/api/rules/drafts/{draft_id}/sandbox"), ("POST", "/api/rules/drafts/{draft_id}/submit-review"),
    ("POST", "/api/rules/drafts/{draft_id}/shadow")}
ORGANISATION_REGISTRY = {("POST", "/api/platform/organisations"), ("PATCH", "/api/platform/organisations/{tenant_id}"),
                         ("POST", "/api/platform/organisations/{tenant_id}/status"),
                         ("POST", "/api/platform/organisations/{tenant_id}/admins")}
# Stage 4b: the Organisation Admin may delegate admin (appoint a TENANT_SECURITY_ADMIN); which roles it may then
# actually assign is decided per role in app/iam.py and tested in tests/test_role_registry.py.
ROLE_REGISTRY_MUTATIONS = {("POST", "/api/role-assignments"), ("POST", "/api/role-assignments/{assignment_id}/revoke")}
# The only state-changing routes each principal may reach; every other one must answer 403 and write nothing.
ALLOWED = {
    "actor-ext-auditor": READ_ONLY_CHECKS,  # ZA-IAM-001 §08: no tenant admin/business mutations
    "actor-agent": set(),  # no route: agent identities are not bound to AuthorityGrants yet
    "actor-audit": READ_ONLY_CHECKS | {("POST", "/api/obligations/{lineage}/snapshots"),
                                       ("POST", "/api/controls/{ci_id}/evaluate"), ("POST", "/api/ledger/checkpoint"),
                                       ("POST", "/api/packages"), ("POST", "/api/ipe/source-runs/{run_id}/evaluate-ipe")},
    "authz-owner": READ_ONLY_CHECKS | {("POST", "/api/org/entities")} | ROLE_REGISTRY_MUTATIONS,  # Organisation Admin
    "actor-admin": READ_ONLY_CHECKS | {("POST", "/api/org/entities"), ("POST", "/api/ledger/checkpoint"),
                                       ("POST", "/api/ipe/source-runs/{run_id}/evaluate-ipe")},  # tenant PLATFORM_ADMIN
    "actor-content-analyst": CONTENT_AUTHOR_ROUTES,
    "authz-super": ORGANISATION_REGISTRY,
}


def test_every_state_changing_route_is_guarded_or_explicitly_public():
    from app.main import ROUTERS
    from app.security import PUBLIC_MUTATIONS, unguarded_mutations
    assert unguarded_mutations(ROUTERS) == []
    assert set(PUBLIC_MUTATIONS) == {"/api/auth/login", "/api/auth/logout", "/api/auth/password",
                                     "/api/packages/verify-upload"}
    assert len(mutating_routes()) >= 60


def test_the_startup_check_sees_every_mounted_router():
    """The startup check reads app.main.ROUTERS; a router mounted any other way would escape it."""
    from app.main import ROUTERS, app
    from app.security import MUTATING_METHODS
    mounted = [r.original_router for r in app.routes if hasattr(r, "original_router")]
    assert mounted, "FastAPI no longer exposes included routers this way; update this check"
    assert {id(r) for r in mounted} == {id(r) for r in ROUTERS}
    direct = [r.path for r in app.routes if isinstance(r, APIRoute) and r.methods & MUTATING_METHODS]
    assert direct == []  # no state-changing route registered directly on the app


def test_an_unguarded_route_is_reported_deny_by_default():
    from app.security import Ctx, authorize, get_ctx, unguarded_mutations
    from fastapi import Depends
    probe = APIRouter()

    @probe.post("/probe/unguarded")
    def unguarded(ctx: Ctx = Depends(get_ctx)):  # authenticated, but no permission declared
        return {}

    @probe.post("/probe/guarded")
    def guarded(ctx: Ctx = Depends(authorize("read"))):
        return {}
    assert unguarded_mutations([probe]) == ["POST /api/probe/unguarded"]


@pytest.mark.parametrize("actor", sorted(ALLOWED))
def test_route_matrix_refuses_everything_not_explicitly_allowed(client, actors, actor):
    before, denied_before = db_fingerprint(), denials(actor)
    wrong, refused = [], 0
    for method, path in mutating_routes():
        if (method, path) in ALLOWED[actor]:
            continue
        r = call(client, method, path, actor)
        refused += 1
        if r.status_code != 403:
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert not wrong, f"{actor} reached routes it must not: {wrong}"
    assert db_fingerprint() == before, f"refused requests by {actor} changed the database"
    assert denials(actor) - denied_before == refused, "every refusal is recorded once, as authorization.denied"


def test_agents_need_a_bound_grant_not_just_an_identity(client):
    # The seeded agent holds a role with broad permissions and its subject has active AuthorityGrants; neither is enough.
    for method, path, body in (("POST", "/api/ai/decisions", {"agent_subject_id": "subj-ai-payables",
                                                              "action_class": "Prepare regulatory filing draft",
                                                              "scope": "UK legal entity; VAT filings only"}),
                               ("POST", "/api/issues/{issue_id}/transition", {"state": "TRIAGED"}),
                               ("POST", "/api/subjects/{subject_id}/facts", {"fact_key": "entity.employee_count",
                                                                             "value": 5,
                                                                             "valid_from": "2025-01-01T00:00:00Z"})):
        r = call(client, method, path, "actor-agent", body)
        assert r.status_code == 403 and "AuthorityGrant" in r.text and "INV-01" in r.text, (path, r.text)


def test_human_only_actions_refuse_service_principals(client):
    before = db_fingerprint()
    exc = client.get("/api/exceptions", headers=H("actor-cco")).json()[0]["exception_id"]
    # actor-svc is a SERVICE: attestation, approvals and holds need a named HUMAN (INV-01) whatever the role says.
    for path, body in ((f"/api/exceptions/{exc}/decide", {"decision": "APPROVE"}),
                       ("/api/evidence/x/hold", {"reason": "probe"}),
                       ("/api/applicability/determinations/x/attest", {"decision": "ADOPT", "authority_basis": "x"})):
        assert client.post(path, headers=H("actor-svc"), json=body).status_code == 403, path
    assert db_fingerprint() == before


def _seed_ids(client):
    issues = client.get("/api/issues", headers=H("actor-cco")).json()
    open_issue = next((i for i in issues if i["state"] == "OPEN"), None)
    if open_issue is None:
        import uuid
        from datetime import datetime, timezone
        from app.db import AdminSession
        from app.models import Issue
        with AdminSession() as db:
            fresh = Issue(
                tenant_id="tenant-zoiko-group",
                title="Authz probe open issue",
                issue_type="PROOF_GAP",
                severity="MEDIUM",
                priority="P2",
                state="OPEN",
                source_ref_type="AssuranceSnapshot",
                source_ref_id=f"probe-snap-{uuid.uuid4().hex[:8]}",
                dedupe_key=f"authz-probe-issue-{uuid.uuid4().hex[:8]}",
                created_at=datetime.now(timezone.utc),
            )
            db.add(fresh)
            db.commit()
            issue_id = fresh.issue_id
    else:
        issue_id = open_issue["issue_id"]

    ids = {"issue": issue_id,
           "evidence": client.get("/api/evidence", headers=H("actor-cco")).json()[0]["evidence_id"],
           "td": None}
    obls = {o["definition"]["key"]: o for o in client.get("/api/obligations", headers=H("actor-cco")).json()}
    ids["dora"] = obls["DORA_ICT_RISK_FRAMEWORK"]["lineage_id"]
    dossier = client.get(f"/api/obligations/{ids['dora']}", headers=H("actor-cco")).json()
    ids["occurrence"] = dossier["occurrences"][0]["occurrence_id"]
    ctl = client.get("/api/controls", headers=H("actor-cco")).json()[0]["control_implementation_id"]
    ids["td"] = client.get(f"/api/controls/{ctl}", headers=H("actor-cco")).json()["tests"][0]["test_definition_id"]
    ids["determination"] = next(d for d in client.get("/api/applicability/determinations", headers=H("actor-cco")).json()
                                if d["state"] in ("PROPOSED", "IN_REVIEW"))["lineage_id"]
    ids["run"] = client.get("/api/ipe/source-runs", headers=H("actor-cco")).json()[0]["source_run_id"]
    ids["installation"] = next(c for c in client.get("/api/connectors", headers=H("actor-cco")).json()
                               if c["installations"])["installations"][0]["installation_id"]
    return ids


def test_other_organisations_cannot_touch_or_see_these_records(client, actors):
    from app.db import AdminSession
    from app.models import EvidenceConflict
    a = _seed_ids(client)
    with AdminSession() as db:  # an OPEN conflict in organisation A, removed again below
        db.add(EvidenceConflict(conflict_id="authz-conflict-a", tenant_id="tenant-zoiko-group",
                                member_evidence_ids=[a["evidence"]], description="cross-tenant probe"))
        db.commit()
    try:
        before = db_fingerprint()
        attempts = [
            ("authz-b-owner", f"/api/issues/{a['issue']}/transition", {"state": "TRIAGED"}),
            ("authz-b-owner", f"/api/occurrences/{a['occurrence']}/fulfil",
             {"fulfilled_at": "2026-07-15T00:00:00Z", "evidence_ids": [a["evidence"]]}),
            ("authz-b-owner", "/api/test-runs", {"test_definition_id": a["td"], "occurred_at": "2026-07-02T00:00:00Z",
                                                "window_from": "2026-07-01T00:00:00Z", "window_to": "2026-07-02T00:00:00Z",
                                                "result": "PASS", "source_system": "probe"}),
            ("authz-b-owner", f"/api/evidence/{a['evidence']}/invalidate", {"reason": "probe"}),
            ("authz-b-cco", "/api/exceptions", {"target_type": "OBLIGATION", "target_id": a["dora"], "reason": "probe",
                                                "valid_from": "2026-07-01T00:00:00Z", "valid_to": "2026-08-01T00:00:00Z"}),
            ("authz-b-cco", f"/api/applicability/determinations/{a['determination']}/attest",
             {"decision": "ADOPT", "authority_basis": "probe"}),
            ("authz-b-cco", "/api/evidence-conflicts/authz-conflict-a/resolve", {"reason": "probe"}),
            ("authz-b-cco", "/api/packages", {"entity_id": "entity-eu", "period_from": "2026-07-01T00:00:00Z",
                                              "period_to": "2026-10-01T00:00:00Z", "obligation_lineage_ids": [a["dora"]]}),
            ("authz-b-audit", f"/api/ipe/source-runs/{a['run']}/evaluate-ipe", {}),
            # another organisation's connector installation (tenant connector configuration / execution state)
            ("authz-b-owner", "/api/ipe/source-runs", {"installation_id": a["installation"], "ipe_policy_version_id": "x",
                                                       "payload": {}}),
        ]
        for actor, path, body in attempts:
            assert client.post(path, headers=H(actor), json=body).status_code == 404, (actor, path)
        # Identifiers supplied in a body are resolved inside the caller's organisation too.
        up = client.post("/api/evidence/upload", headers=H("authz-b-owner"), files={"file": ("x.txt", b"probe", "text/plain")},
                         data={"entity_id": "entity-eu", "title": "probe", "coverage_from": "2026-07-01T00:00:00Z",
                               "coverage_to": "2026-08-01T00:00:00Z"})
        assert up.status_code == 404
        assert client.get(f"/api/evidence/{a['evidence']}", headers=H("authz-b-cco")).status_code == 404
        assert db_fingerprint() == before
    finally:
        # Governance projections refuse DELETE for every role (migration 0010); only this test's own probe row is
        # removed, by switching the guard off inside this one transaction.
        from app.db import IS_POSTGRES
        with AdminSession() as db:
            if IS_POSTGRES:
                db.execute(text("ALTER TABLE evidence_conflict DISABLE TRIGGER immutable_columns"))
            db.execute(delete(EvidenceConflict).where(EvidenceConflict.conflict_id == "authz-conflict-a"))
            if IS_POSTGRES:
                db.execute(text("ALTER TABLE evidence_conflict ENABLE TRIGGER immutable_columns"))
            db.commit()


def test_client_supplied_identifiers_must_belong_to_the_caller(client, actors):
    a = _seed_ids(client)
    before = db_fingerprint()
    cases = [
        (client.post("/api/evidence/upload", headers=H("actor-owner"), files={"file": ("x.txt", b"probe", "text/plain")},
                     data={"entity_id": "entity-authz-b", "title": "probe", "coverage_from": "2026-07-01T00:00:00Z",
                           "coverage_to": "2026-08-01T00:00:00Z"}), 404),
        (client.post("/api/ai/assets", headers=H("actor-aigov"), json={
            "entity_id": "no-such-entity", "display_name": "probe", "asset_kind": "AGENT", "version_label": "1",
            "purpose": "probe", "provider": "x", "model": "y", "environment": "dev"}), 404),
        (client.post("/api/exceptions", headers=H("actor-cco"), json={
            "target_type": "OBLIGATION", "target_id": "no-such-obligation", "reason": "probe",
            "valid_from": "2026-07-01T00:00:00Z", "valid_to": "2026-08-01T00:00:00Z"}), 404),
        (client.post("/api/exceptions", headers=H("actor-cco"), json={
            "target_type": "TENANT", "target_id": a["dora"], "reason": "probe",
            "valid_from": "2026-07-01T00:00:00Z", "valid_to": "2026-08-01T00:00:00Z"}), 422),
        (client.post("/api/test-runs", headers=H("actor-owner"), json={
            "test_definition_id": a["td"], "occurred_at": "2026-07-02T00:00:00Z", "window_from": "2026-07-01T00:00:00Z",
            "window_to": "2026-07-02T00:00:00Z", "result": "PASS", "source_system": "probe",
            "source_run_id": "no-such-run"}), 404),
        (client.post("/api/ai/decisions", headers=H("actor-svc"), json={
            "agent_subject_id": "subj-authz-b", "action_class": "x", "scope": "y"}), 404),
        (client.post("/api/packages", headers=H("actor-audit"), json={
            "entity_id": "entity-authz-b", "period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z",
            "obligation_lineage_ids": [a["dora"]]}), 404),
    ]
    assert [r.status_code for r, _ in cases] == [want for _, want in cases], [r.text for r, _ in cases]
    assert db_fingerprint() == before


def test_authorised_workflows_still_work(client, actors):
    from app.db import AdminSession
    from app.models import AuditEvent
    a = _seed_ids(client)
    # Control Owner works an issue (manage_issues).
    r = client.post(f"/api/issues/{a['issue']}/transition", headers=H("actor-owner"), json={"state": "TRIAGED"})
    assert r.status_code == 200 and r.json()["state"] == "TRIAGED"
    # CCO requests an exception; the requester cannot approve it; a Risk Approver can.
    x = client.post("/api/exceptions", headers=H("actor-cco"), json={
        "target_type": "OBLIGATION", "target_id": a["dora"], "reason": "authorised workflow probe",
        "valid_from": "2026-07-01T00:00:00Z", "valid_to": "2026-07-02T00:00:00Z"})
    assert x.status_code == 200
    xid = x.json()["exception_id"]
    assert client.post(f"/api/exceptions/{xid}/decide", headers=H("actor-cco"), json={"decision": "APPROVE"}).status_code == 403
    assert client.post(f"/api/exceptions/{xid}/decide", headers=H("actor-risk"), json={"decision": "APPROVE"}).json()["state"] == "APPROVED"
    # An occurrence is added and fulfilled with evidence by the Control Owner.
    occ = client.post(f"/api/obligations/{a['dora']}/occurrences", headers=H("actor-cco"),
                      json={"window_from": "2026-11-01T00:00:00Z", "due_at": "2026-11-30T00:00:00Z"}).json()
    f = client.post(f"/api/occurrences/{occ['occurrence_id']}/fulfil", headers=H("actor-owner"),
                    json={"fulfilled_at": "2026-11-20T00:00:00Z", "evidence_ids": [a["evidence"]]})
    assert f.status_code == 200
    # A conflict is opened by the Control Owner and resolved, once, by an assurance reviewer; the resolution is audited.
    c = client.post("/api/evidence-conflicts", headers=H("actor-owner"),
                    json={"member_evidence_ids": [a["evidence"]], "description": "authorised workflow probe"}).json()
    assert client.post(f"/api/evidence-conflicts/{c['conflict_id']}/resolve", headers=H("actor-owner"),
                       json={"reason": "x"}).status_code == 403  # opening is not resolving
    assert client.post(f"/api/evidence-conflicts/{c['conflict_id']}/resolve", headers=H("actor-cco"),
                       json={"reason": "source A is authoritative"}).status_code == 200
    assert client.post(f"/api/evidence-conflicts/{c['conflict_id']}/resolve", headers=H("actor-cco"),
                       json={"reason": "again"}).status_code == 409
    with AdminSession() as db:
        ev = db.execute(select(AuditEvent).where(AuditEvent.action == "evidence_conflict.resolved",
                                                 AuditEvent.object_id == c["conflict_id"])).scalars().one()
        assert (ev.actor_id, ev.prior_state, ev.new_state, ev.tenant_id) == ("actor-cco", "OPEN", "RESOLVED", "tenant-zoiko-group")
    # IPE anomaly disposition by a Risk Approver (assurance_review).
    anomaly = client.get("/api/ipe/anomalies", headers=H("actor-cco")).json()[0]["anomaly_id"]
    assert client.post(f"/api/ipe/anomalies/{anomaly}/dispositions", headers=H("actor-risk"),
                       json={"disposition_code": "SOURCE_DEFECT", "rationale": "probe"}).status_code == 200


# ------------------------------------------------------------------------------------------------ F1: bound IPE results
def _failed_run(client):
    runs = client.get("/api/ipe/source-runs", headers=H("actor-cco")).json()
    run = next(r for r in runs if r["ipe"]["overall_result"] == "IPE_FAILED")
    pols = {p["ipe_policy_version_id"]: p for p in client.get("/api/ipe/policies", headers=H("actor-cco")).json()}
    weaker = next(pid for pid in pols if pid != run["ipe_policy_version_id"])
    return run, weaker


def _mfa_leaves(snapshot):
    return sorted({(leaf["control_state"], tuple(leaf["reason_codes"])) for seg in snapshot["segments"]
                   for leaf in seg["leaf_evaluations"] if leaf["control_key"] == "C_MFA_PHISHING_RESISTANT"})


def test_ipe_reevaluation_needs_an_authorised_human(client):
    run, _ = _failed_run(client)
    before = db_fingerprint()
    for actor in ("actor-ext-auditor", "actor-agent", "actor-svc", "actor-owner", "actor-content-analyst"):
        r = client.post(f"/api/ipe/source-runs/{run['source_run_id']}/evaluate-ipe", headers=H(actor),
                        json={"ipe_policy_version_id": run["ipe_policy_version_id"]})
        assert r.status_code == 403, (actor, r.text)
    assert db_fingerprint() == before


def test_ipe_service_function_checks_authorization_itself(client):
    from app.db import AdminSession
    from app.engines import ipe
    from app.models import Actor, SourceRun
    from app.security import Ctx
    run, _ = _failed_run(client)
    before = db_fingerprint()
    with AdminSession() as db:
        for actor in ("actor-ext-auditor", "actor-owner"):
            with pytest.raises(HTTPException) as e:
                ipe.reevaluate(Ctx(db=db, actor=db.get(Actor, actor)), db.get(SourceRun, run["source_run_id"]))
            assert e.value.status_code == 403
        db.rollback()
    assert db_fingerprint() == before


def test_weaker_policy_cannot_replace_the_bound_result(client):
    run, weaker = _failed_run(client)
    rid = run["source_run_id"]
    original = client.get(f"/api/ipe/source-runs/{rid}", headers=H("actor-audit")).json()
    before = db_fingerprint()
    r = client.post(f"/api/ipe/source-runs/{rid}/evaluate-ipe", headers=H("actor-audit"),
                    json={"ipe_policy_version_id": weaker})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "IPE_POLICY_NOT_BOUND"
    assert db_fingerprint() == before

    # A legitimate re-evaluation under the run's own policy is recorded beside the original, which stays bound.
    ok = client.post(f"/api/ipe/source-runs/{rid}/evaluate-ipe", headers=H("actor-audit"), json={})
    assert ok.status_code == 200
    assert (ok.json()["ipe_policy_version_id"], ok.json()["overall_result"]) == (run["ipe_policy_version_id"], "IPE_FAILED")
    after = client.get(f"/api/ipe/source-runs/{rid}", headers=H("actor-audit")).json()
    assert after["bound_ipe_evaluation"]["ipe_evaluation_id"] == original["bound_ipe_evaluation"]["ipe_evaluation_id"]
    assert len(after["ipe_evaluations"]) == len(original["ipe_evaluations"]) + 1
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        audit = db.execute(select(AuditEvent).where(AuditEvent.action == "ipe.reevaluated", AuditEvent.object_id == rid)
                           .order_by(AuditEvent.record_seq.desc())).scalars().first()
        assert (audit.actor_id, audit.prior_state, audit.new_state) == ("actor-audit", "IPE_FAILED", "IPE_FAILED")


def test_assurance_ignores_newer_or_weaker_ipe_results(client):
    """Simulates IPE_VERIFIED rows written before this fix (under a weaker policy, and later under the bound one):
    the control stays NOT_EVALUATED/IPE_FAILED, and a snapshot taken before them still reproduces."""
    from app.db import AdminSession
    from app.models import IPEEvaluation, SourceRun
    run, weaker = _failed_run(client)
    rid = run["source_run_id"]
    dora = next(o for o in client.get("/api/obligations", headers=H("actor-cco")).json()
                if o["definition"]["key"] == "DORA_ICT_RISK_FRAMEWORK")["lineage_id"]
    q3 = {"period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z"}
    snap = client.post(f"/api/obligations/{dora}/snapshots", headers=H("actor-cco"), json=q3).json()
    assert ("NOT_EVALUATED", ("IPE_FAILED",)) in _mfa_leaves(snap)
    planted = []
    with AdminSession() as db:
        r = db.get(SourceRun, rid)
        for policy in (weaker, r.ipe_policy_version_id):
            ev = IPEEvaluation(tenant_id=r.tenant_id, source_run_id=rid, ipe_policy_version_id=policy,
                               mapping_contract_version_id=r.mapping_contract_version_id, completeness_result="VERIFIED",
                               accuracy_result="VERIFIED", overall_result="IPE_VERIFIED", checks=[], reason_codes=[],
                               engine_version="planted-by-test")
            db.add(ev)
            db.flush()
            planted.append(ev.ipe_evaluation_id)
        db.commit()
    try:
        preview = client.post(f"/api/obligations/{dora}/preview", headers=H("actor-cco"), json=q3).json()
        assert _mfa_leaves(preview) == _mfa_leaves(snap)
        assert ("OPERATING", ()) != tuple(_mfa_leaves(preview))  # never all-OPERATING from a planted result
        rep = client.get(f"/api/snapshots/{snap['snapshot_id']}/reproduce", headers=H("actor-audit")).json()
        assert rep["reproducible"] and rep["reproduced_state"] == snap["state"]
        listed = next(x for x in client.get("/api/ipe/source-runs", headers=H("actor-cco")).json() if x["source_run_id"] == rid)
        assert listed["ipe"]["overall_result"] == "IPE_FAILED"
        detail = client.get(f"/api/ipe/source-runs/{rid}", headers=H("actor-cco")).json()
        assert detail["bound_ipe_evaluation"]["overall_result"] == "IPE_FAILED"
    finally:
        # Completed IPE results are immutable (IPE-REQ-025; migration 0003). Even the owner role can remove these
        # test-planted rows only by switching the guard off explicitly, inside this one transaction.
        from app.db import IS_POSTGRES
        with AdminSession() as db:
            if IS_POSTGRES:
                db.execute(text("ALTER TABLE evidence_ipe_evaluation DISABLE TRIGGER append_only"))
            db.execute(delete(IPEEvaluation).where(IPEEvaluation.ipe_evaluation_id.in_(planted)))
            if IS_POSTGRES:
                db.execute(text("ALTER TABLE evidence_ipe_evaluation ENABLE TRIGGER append_only"))
            db.commit()


# ------------------------------------------------------------------------------------------------ F6: the registry
def _connector(client, key="vanta"):
    return next(c for c in client.get("/api/connectors", headers=H("actor-admin")).json() if c["key"] == key)


def test_only_zoiko_certification_staff_can_change_the_global_registry(client, actors):
    c = _connector(client)
    before = db_fingerprint()
    body = {"certification": "SUSPENDED", "runtime_health": "FAILED", "reason": "probe"}
    for actor in ("actor-admin", "actor-cco", "authz-owner", "authz-b-admin", "authz-super", "authz-certifier-svc",
                  "actor-agent", "actor-ext-auditor", "actor-content-analyst"):
        r = client.post(f"/api/connectors/{c['connector_id']}/certification", headers=H(actor), json=body)
        assert r.status_code == 403, (actor, r.text)
    assert db_fingerprint() == before
    after = _connector(client)
    assert (after["certification"], after["runtime_health"]) == (c["certification"], c["runtime_health"])


def test_registry_service_function_refuses_tenant_principals(client):
    from app.db import AdminSession
    from app.engines import connectors
    from app.models import Actor
    from app.security import Ctx
    c = _connector(client)
    before = db_fingerprint()
    with AdminSession() as db:
        for actor in ("actor-admin", "actor-cco"):
            with pytest.raises(HTTPException) as e:
                connectors.set_certification(Ctx(db=db, actor=db.get(Actor, actor)), c["connector_id"],
                                             certification="SUSPENDED", runtime_health="FAILED", reason="probe")
            assert e.value.status_code == 403
        db.rollback()
    assert db_fingerprint() == before


def test_authorised_certification_change_is_validated_and_audited(client, actors):
    from app.db import AdminSession
    from app.models import AuditEvent
    c = _connector(client)
    url = f"/api/connectors/{c['connector_id']}/certification"
    hdr = H("authz-certifier")
    aws = _connector(client, "aws-config")  # its certification matrix has a FAIL row (FR-CON-002)
    assert client.post(f"/api/connectors/{aws['connector_id']}/certification", headers=hdr,
                       json={"certification": "CERTIFIED", "reason": "probe"}).status_code == 409
    assert client.post(url, headers=hdr, json={"certification": "TOTALLY_FINE", "reason": "probe"}).status_code == 422
    assert client.post(url, headers=hdr, json={"certification": c["certification"], "runtime_health": "DEGRADED",
                                               "reason": " "}).status_code == 422
    try:
        r = client.post(url, headers=hdr, json={"certification": c["certification"], "runtime_health": "DEGRADED",
                                                "reason": "Vendor API latency incident"})
        assert r.status_code == 200 and r.json()["runtime_health"] == "DEGRADED"
        with AdminSession() as db:
            ev = db.execute(select(AuditEvent).where(AuditEvent.action == "connector.certification_changed",
                                                     AuditEvent.object_id == c["connector_id"])
                            .order_by(AuditEvent.record_seq.desc())).scalars().first()
            assert ev.actor_id == "authz-certifier" and ev.tenant_id is None and ev.recorded_at is not None
            assert (ev.prior_state, ev.new_state) == (f"{c['certification']}|{c['runtime_health']}",
                                                      f"{c['certification']}|DEGRADED")
            assert ev.reason == "Vendor API latency incident"
    finally:
        client.post(url, headers=hdr, json={"certification": c["certification"], "runtime_health": c["runtime_health"],
                                            "reason": "restore after test"})
    restored = _connector(client)
    assert (restored["certification"], restored["runtime_health"]) == (c["certification"], c["runtime_health"])
