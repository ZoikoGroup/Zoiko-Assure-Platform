"""Organisation and Super Admin sidebar <-> backend integration (docs/sidebar-backend-integration-matrix.md).

What this proves, per the matrix:

* Contract: every API call in the frontend source matches a backend route and method; every sidebar screen's
  mount-time reads (frontend/lib/screen-access.json) exist, and the permissions that make its sidebar item visible are
  exactly what the backend guards of those reads accept (ZA-API-001 API-REQ-042 consumer contract).
* Visibility = authorization: for one principal per role, every screen the sidebar shows loads without a refusal, and
  every screen it hides is refused by the backend on direct access (ZA-IAM-001 IAM-REQ-007, deny by default).
* Organisation screens: sign-in required (401), no cross-tenant data (ZA-TEN-001 TEN-REQ-021/022, QA §09), Super Admin
  and content staff refused (INV-14, IAM-REQ-024).
* Super Admin registry: platform principals only (organisation roles refused and recorded), allowlisted filters
  (API-REQ-023), search text kept out of the audit record (ZA-API-001 §11).
* Team & Access: the identity directory for role administrators with typed filters (WF-IAM-01); session listing and
  revocation by the principal itself or its organisation's security administrator (IAM §23, IAM-REQ-015), audited.
* Audit Trail: cursor pagination with a stable total order, pinned watermark and signed, scope- and query-bound
  cursors (ZA-API-001 §04/§11, API-REQ-012).
* Org Settings: entity registration validates its input (API-REQ-014/033) and is audited (IAM-REQ-037).
* Overview: the "Obligations due / late" figure is a recount of recorded occurrences, not synthetic.

Regenerate the `perms` and `roles` of frontend/lib/screen-access.json deliberately with:
    python tests/test_sidebar_integration.py      (from backend/)
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import denials

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
ACCESS = FRONTEND / "lib" / "screen-access.json"
NAV = FRONTEND / "components" / "nav.ts"
# Sidebar screens that load no backend data when they open: self-service or static pages, shown to their personas.
NO_READS = {"/", "/admin/users", "/admin/identity"}

TENANT, TA, TB = "tenant-zoiko-group", "tenant-sb-a", "tenant-sb-b"
SB_OWNER, SB_TSA, SB_SUPER = "sb-owner", "sb-tsa", "sb-super"           # in the seeded tenant / the platform
A_OWNER, A_TSA, A_USER, A_USER2 = "sb-a-owner", "sb-a-tsa", "sb-a-user", "sb-a-user2"
B_TSA, B_USER = "sb-b-tsa", "sb-b-user"


# ------------------------------------------------------------------------------------------------ registry helpers
def _operations():
    from app.main import ROUTERS
    from app.security import operations
    return operations(ROUTERS)


def _segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def _template_matches(template: str, path: str) -> bool:
    """Whether a frontend path ("{}" marks a dynamic segment) can reach a backend route template."""
    t, p = _segments(template), _segments(path)
    return len(t) == len(p) and all(ts == ps or (ts.startswith("{") and ts.endswith("}")) or ps == "{}"
                                    for ts, ps in zip(t, p))


def _get_operation(path: str):
    found = [o for o in _operations() if o.method == "GET" and o.path == "/api" + path]
    assert len(found) == 1, f"no single GET operation for {path}: {[o.operation_id for o in found]}"
    return found[0]


def screen_perms(reads: list[str]) -> list[str]:
    """The permissions every listed read accepts (public reads constrain nothing)."""
    out: set[str] | None = None
    for path in reads:
        op = _get_operation(path)
        if op.public is not None:
            continue
        assert not op.requirement.platform and not op.requirement.human, f"{path}: screen reads need no platform/human flag"
        out = set(op.requirement.permissions) if out is None else out & set(op.requirement.permissions)
    return sorted(out or set())


def tenant_role_permissions() -> dict[str, list[str]]:
    """The permissions each organisation role a person can hold confers (ROLE_PERMISSIONS), so the frontend can name
    which roles may take an action its user cannot. Workload identities (SERVICE) are left out: no person holds them."""
    from app.iam import ASSIGNABILITY
    from app.security import ROLE_PERMISSIONS
    codes = [c for c in ASSIGNABILITY if c != "SERVICE"] + ["TENANT_OWNER"]
    return {c: sorted(ROLE_PERMISSIONS[c]) for c in codes}


def _access() -> dict:
    return json.loads(ACCESS.read_text(encoding="utf-8"))["screens"]


# ------------------------------------------------------------------------------------------------ frontend scanning
def _skip_string(src: str, i: int) -> int:
    """Index just past the string/template literal starting at src[i]."""
    q, i = src[i], i + 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if q == "`" and src.startswith("${", i):
            i = _skip_braces(src, i + 1)
            continue
        if c == q:
            return i + 1
        i += 1
    return i


def _skip_braces(src: str, i: int) -> int:
    """Index just past the {...} block starting at src[i] (strings inside it are skipped)."""
    depth = 0
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            i = _skip_string(src, i)
            continue
        depth += c == "{"
        depth -= c == "}"
        i += 1
        if depth == 0:
            return i
    return i


def _call_args(src: str, open_paren: int) -> str:
    depth, i = 0, open_paren
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            i = _skip_string(src, i)
            continue
        depth += c == "("
        depth -= c == ")"
        i += 1
        if depth == 0:
            return src[open_paren + 1:i - 1]
    return src[open_paren + 1:]


def _first_arg(args: str) -> str:
    depth, i = 0, 0
    while i < len(args):
        c = args[i]
        if c in "\"'`":
            i = _skip_string(args, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            return args[:i]
        i += 1
    return args


def _normalise(literal: str) -> str | None:
    """'/a/${x}/b?y=1' -> '/a/{}/b'; None when the whole path is dynamic."""
    out, i = "", 0
    while i < len(literal):
        if literal.startswith("${", i):
            end = _skip_braces(literal, i + 1)
            out += "{}" if literal[i + 2:end - 1].strip() != "API" else "\x00"
            i = end
        else:
            out += literal[i]
            i += 1
    out = out.replace("\x00", "")
    out = out.split("?", 1)[0].rstrip("/")
    # `/actors${query ? `?${query}` : ""}`: a placeholder glued to the end of the last literal segment is a dynamic
    # suffix (an optional query string), not part of the path.
    head, _, last = out.rpartition("/")
    if last.endswith("{}") and last != "{}":
        out = f"{head}/{last[:-2]}"
    return out if out.startswith("/") and not out.startswith("/{}") else None


CALL = re.compile(r"\b(useApi|api|download|fetch|uploadForm)\s*(?:<(?:[^<>]|<[^<>]*>)*>)?\s*\(")


def frontend_calls() -> list[tuple[str, str, str]]:
    """(method, normalised path, "file:line") for every API call with a literal path in the frontend source."""
    out = []
    for f in sorted([*FRONTEND.glob("app/**/*.ts*"), *FRONTEND.glob("components/**/*.ts*"), *FRONTEND.glob("lib/**/*.ts*")]):
        src = f.read_text(encoding="utf-8")
        for m in CALL.finditer(src):
            fn, args = m.group(1), _call_args(src, m.end() - 1)
            first = _first_arg(args)
            literals = []
            j = 0
            while j < len(first):
                if first[j] in "\"'`":
                    end = _skip_string(first, j)
                    literals.append(first[j + 1:end - 1])
                    j = end
                else:
                    j += 1
            if fn == "fetch":
                literals = [x for x in literals if x.startswith("${API}")]
            method = "POST" if fn == "uploadForm" else "GET"  # lib/api.ts uploadForm always POSTs
            if fn in ("api", "fetch"):
                mm = re.search(r"\bmethod\s*:\s*[\"'](\w+)[\"']", args)
                method = mm.group(1).upper() if mm else "GET"
            for lit in literals:
                path = _normalise(lit)
                if path:
                    out.append((method, path, f"{f.relative_to(FRONTEND).as_posix()}:{src.count(chr(10), 0, m.start()) + 1}"))
    return out


def test_every_frontend_api_call_matches_a_backend_route_and_method():
    ops = _operations()
    calls = frontend_calls()
    assert len(calls) >= 120, f"the scanner found only {len(calls)} calls; it has stopped seeing the frontend's API use"
    wrong = [f"{method} {path} ({where})" for method, path, where in calls
             if not any(o.method == method and _template_matches(o.path, "/api" + path) for o in ops)]
    assert not wrong, f"frontend calls with no backend route: {wrong}"


def test_the_scanner_sees_the_calls_this_task_wired():
    found = {(m, p) for m, p, _ in frontend_calls()}
    for call in (("GET", "/audit-events"), ("GET", "/sessions"), ("POST", "/sessions/{}/revoke"),
                 ("GET", "/principals/{}/sessions"), ("GET", "/roles"), ("GET", "/role-assignments"),
                 ("POST", "/role-assignments"), ("POST", "/role-assignments/{}/revoke"),
                 ("GET", "/principals/{}/effective-access"), ("GET", "/platform/organisations"),
                 ("POST", "/evidence/upload")):
        assert call in found, call


# ------------------------------------------------------------------------------------------------ sidebar contract
def test_screen_access_is_generated_from_the_backend_guards():
    """Sidebar visibility is derived from authorization, never decided separately (IAM-REQ-007)."""
    nav = NAV.read_text(encoding="utf-8")
    hrefs = set(re.findall(r'\{ href: "(/[^"]*)", label:', nav.split("const ORG_NAV", 1)[0]))
    screens = _access()
    assert set(screens) | NO_READS == hrefs, "every sidebar screen is either listed with its reads or has none"
    doc = json.loads(ACCESS.read_text(encoding="utf-8"))
    every = {**screens, **{f"shell.{k}": v for k, v in doc["shell"].items()},
             **{f"panels.{k}": v for k, v in doc["panels"].items()}}
    stale = {href: (s["perms"], screen_perms(s["reads"])) for href, s in every.items()
             if s["perms"] != screen_perms(s["reads"])}
    assert not stale, f"regenerate frontend/lib/screen-access.json and review: {stale}"
    assert all(s["perms"] for s in every.values()), "reads that no permission satisfies can never be made"
    # "Which roles can do this" in the frontend's permission notes is the backend catalogue, never a separate list.
    assert doc["roles"] == tenant_role_permissions(), "regenerate frontend/lib/screen-access.json (roles)"


@pytest.mark.parametrize("actor", ["actor-cco", "actor-ext-auditor", "sb-tsa", "sb-super", "actor-content-analyst"])
def test_shell_reads_are_made_only_when_they_will_be_served(client, actor, request):
    """The header's watermark poll and the organisation frame's alert/entity counts run on every page; for a person
    without their permission they would record a refusal every 10 seconds, so the frontend skips them (IAM-REQ-037
    noise) and the backend refuses them if called anyway."""
    request.getfixturevalue("world")
    perms = set(client.get("/api/me", headers=H(actor)).json()["permissions"])
    for name, read in json.loads(ACCESS.read_text(encoding="utf-8"))["shell"].items():
        before = denials(actor)
        status = client.get(f"/api{read['reads'][0]}", headers=H(actor)).status_code
        served = bool(perms & set(read["perms"]))
        assert (status == 200) == served, (actor, name, status)
        # What an ungated 10-second poll would cost: one authorization.denied audit record per refused call.
        assert denials(actor) - before == (0 if served else 1), (actor, name)


# ------------------------------------------------------------------------------------------------ principals
def _credential(db, actor_id):
    """A sign-in credential with this test session's password, as conftest gives the seeded accounts."""
    from app.auth import DEMO_PASSWORD, hash_password
    from app.models import AuthCredential
    db.flush()
    if db.get(AuthCredential, actor_id) is None:
        db.add(AuthCredential(actor_id=actor_id, password_hash=hash_password(DEMO_PASSWORD)))


@pytest.fixture(scope="module")
def world(client):
    """Two probe organisations (each with an Organisation Admin, a security admin and readers), role principals in the
    seeded organisation, a Super Admin, and one ADOPTED obligation with recorded occurrences in organisation A."""
    from app.db import CELL_ID, AdminSession
    from app.models import (Actor, LegalEntity, ObligationDefinition, ObligationInstance, ObligationOccurrence,
                            Subject, Tenant)
    now = datetime.now(timezone.utc)
    with AdminSession() as db:
        for tid, name in ((TA, "Sidebar Probe A Group"), (TB, "Sidebar Probe B Group")):
            if db.get(Tenant, tid) is None:
                db.add(Tenant(tenant_id=tid, legal_name=name, contracting_region="UK"))
                db.flush()
                db.add(LegalEntity(entity_id=f"entity-{tid}", tenant_id=tid, legal_name=f"{name} Ltd", country="GB",
                                   home_cell_id=CELL_ID, subject_id=f"subj-{tid}"))
                db.add(Subject(subject_id=f"subj-{tid}", tenant_id=tid, entity_id=f"entity-{tid}",
                               subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        for aid, tenant, roles in ((SB_OWNER, TENANT, ["TENANT_OWNER"]), (SB_TSA, TENANT, ["TENANT_SECURITY_ADMIN"]),
                                   (SB_SUPER, None, ["SUPER_ADMIN"]),
                                   (A_OWNER, TA, ["TENANT_OWNER"]), (A_TSA, TA, ["TENANT_SECURITY_ADMIN"]),
                                   (A_USER, TA, ["CCO"]), (A_USER2, TA, ["CCO"]),
                                   (B_TSA, TB, ["TENANT_SECURITY_ADMIN"]), (B_USER, TB, ["CCO"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=f"Probe {aid}",
                              email=f"{aid}@sidebar.example", roles=roles)
            _credential(db, aid)
        if db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == TA)).first() is None:
            odef = db.execute(select(ObligationDefinition).limit(1)).scalars().first()
            for lineage, lifecycle in (("sb-lineage-adopted", "ADOPTED"), ("sb-lineage-other", "WITHDRAWN")):
                db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=f"entity-{TA}",
                                          subject_id=f"subj-{TA}", obligation_definition_id=odef.obligation_definition_id,
                                          determination_revision_id="sb-det", attestation_id="sb-att",
                                          lifecycle=lifecycle, valid_from=now - timedelta(days=400), record_from_seq=1))
            for lineage, due, fulfilled in (("sb-lineage-adopted", now - timedelta(days=2), None),      # overdue
                                            ("sb-lineage-adopted", now + timedelta(days=30), None),     # due later
                                            ("sb-lineage-adopted", now - timedelta(days=40), now - timedelta(days=41)),
                                            ("sb-lineage-other", now - timedelta(days=2), None)):       # not ADOPTED
                db.add(ObligationOccurrence(tenant_id=TA, obligation_lineage_id=lineage, window_from=due - timedelta(days=30),
                                            due_at=due, fulfilled_at=fulfilled, record_seq=1))
        db.commit()
    return True


def _login(client, actor_id) -> str:
    from app.auth import DEMO_PASSWORD
    r = client.post("/api/auth/login", json={"email": f"{actor_id}@sidebar.example", "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _latest_audit(action, **where):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        q = select(AuditEvent).where(AuditEvent.action == action)
        for k, v in where.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return db.execute(q.order_by(AuditEvent.record_seq.desc())).scalars().first()


# ------------------------------------------------------------------------------------------------ visibility = authz
ROLE_PRINCIPALS = ["actor-cco", "actor-gc", "actor-analyst", "actor-owner", "actor-audit", "actor-aigov", "actor-admin",
                   "actor-risk", "actor-ext-auditor", SB_OWNER, SB_TSA, SB_SUPER, "actor-content-analyst",
                   "actor-encoder", "actor-content-legal", "actor-release"]


@pytest.mark.parametrize("actor", ROLE_PRINCIPALS)
def test_each_visible_screen_loads_and_each_hidden_screen_is_refused(client, world, actor):
    """The sidebar shows a screen exactly when the backend serves all of its reads (organisation-wide permissions)."""
    perms = set(client.get("/api/me", headers=H(actor)).json()["permissions"])
    wrong = []
    for href, screen in _access().items():
        visible = bool(perms & set(screen["perms"]))
        codes = {path: client.get(f"/api{path}", headers=H(actor)).status_code for path in screen["reads"]}
        if visible and any(c in (401, 403) for c in codes.values()):
            wrong.append(f"{href} shown but refused: {codes}")
        if not visible and not any(c == 403 for c in codes.values()):
            wrong.append(f"{href} hidden but served: {codes}")
    assert not wrong, wrong


def test_hidden_screens_cover_the_role_administrators_and_platform_staff(client, world):
    """A security administrator sees the directory but no business screen; the Super Admin sees no organisation screen."""
    for actor in (SB_TSA, SB_SUPER):
        perms = set(client.get("/api/me", headers=H(actor)).json()["permissions"])
        assert not any(perms & set(s["perms"]) for s in _access().values()), actor
    assert client.get("/api/actors", headers=H(SB_TSA)).status_code == 200


# ------------------------------------------------------------------------------------------------ organisation APIs
def test_organisation_screens_need_sign_in_and_never_cross_tenants(client, world):
    reads = sorted({p for s in _access().values() for p in s["reads"]})
    for path in reads:
        op = _get_operation(path)
        if op.public is None:
            assert client.get(f"/api{path}").status_code == 401, path
        r = client.get(f"/api{path}", headers=H(B_USER))
        assert r.status_code == 200, (path, r.status_code)
        assert TENANT not in r.text and TA not in r.text, f"{path} returned another organisation's records"
    # Direct object access with another organisation's identifiers answers 404, exactly like a missing record.
    ev = client.get("/api/evidence", headers=H("actor-cco")).json()[0]["evidence_id"]
    assert client.get(f"/api/evidence/{ev}", headers=H(B_USER)).status_code == 404


def test_organisation_screens_refuse_platform_staff_and_record_it(client, world):
    for actor in (SB_SUPER, "actor-content-analyst"):
        before = denials(actor)
        for path in ("/org/tenant", "/dashboard", "/evidence", "/audit-events", "/ledger"):
            assert client.get(f"/api{path}", headers=H(actor)).status_code == 403, (actor, path)
        assert denials(actor) - before == 5


# ------------------------------------------------------------------------------------------------ Super Admin APIs
SUPER_READS = ["/api/platform/organisations", f"/api/platform/organisations/{TENANT}"]


def test_super_admin_endpoints_are_platform_only(client, world):
    for path in SUPER_READS:
        assert client.get(path).status_code == 401
        for actor in ("actor-cco", SB_OWNER, SB_TSA, "actor-admin", "actor-ext-auditor", "actor-content-analyst"):
            before = denials(actor)
            assert client.get(path, headers=H(actor)).status_code == 403, (actor, path)
            assert denials(actor) - before == 1
        assert client.get(path, headers=H(SB_SUPER)).status_code == 200
    # Organisation roles cannot reach the registry's mutations either, whatever they send.
    for method, path, body in (("POST", "/api/platform/organisations", {"legal_name": "X"}),
                               ("PATCH", f"/api/platform/organisations/{TENANT}", {"legal_name": "Renamed"}),
                               ("POST", f"/api/platform/organisations/{TENANT}/status", {"status": "SUSPENDED", "reason": "x"}),
                               ("POST", f"/api/platform/organisations/{TENANT}/admins", {"display_name": "Y", "email": "y@x.io"})):
        for actor in (SB_OWNER, SB_TSA):
            assert client.request(method, path, headers=H(actor), json=body).status_code == 403, (actor, method, path)
    detail = client.get(f"/api/platform/organisations/{TENANT}", headers=H(SB_SUPER)).json()
    assert detail["tenant_id"] == TENANT and "admin_events" in detail
    assert "evidence" not in detail and "facts" not in detail  # registry metadata only (INV-14)


def test_super_admin_registry_filters(client, world):
    tag = secrets.token_hex(4)
    created = client.post("/api/platform/organisations", headers=H(SB_SUPER), json={
        "legal_name": f"Filter Probe {tag} Holdings", "entity": {"legal_name": f"Filter Probe {tag} Ltd", "country": "GB"},
        "admin": {"display_name": "Filter Admin", "email": f"filter-{tag}@sidebar.example"}})
    assert created.status_code == 201, created.text
    tid = created.json()["tenant_id"]
    assert client.post(f"/api/platform/organisations/{tid}/status", headers=H(SB_SUPER),
                       json={"status": "SUSPENDED", "reason": "filter probe"}).status_code == 200

    everything = client.get("/api/platform/organisations", headers=H(SB_SUPER)).json()
    suspended = client.get("/api/platform/organisations", params={"status": "SUSPENDED"}, headers=H(SB_SUPER)).json()
    active = client.get("/api/platform/organisations", params={"status": "ACTIVE"}, headers=H(SB_SUPER)).json()
    assert {o["tenant_id"] for o in suspended} | {o["tenant_id"] for o in active} == {o["tenant_id"] for o in everything}
    assert tid in {o["tenant_id"] for o in suspended} and all(o["status"] == "SUSPENDED" for o in suspended)
    assert all(o["status"] == "ACTIVE" for o in active)
    found = client.get("/api/platform/organisations", params={"q": f"PROBE {tag.upper()}"}, headers=H(SB_SUPER)).json()
    assert [o["tenant_id"] for o in found] == [tid]
    assert found[0]["entities"] and found[0]["admins"]  # the same record shape as the unfiltered list
    assert client.get("/api/platform/organisations", params={"q": "no-such-organisation-" + tag},
                      headers=H(SB_SUPER)).json() == []
    # The read is recorded with the filters applied, never the search text (ZA-API-001 §11).
    rec = _latest_audit("platform.organisation_registry_read", actor_id=SB_SUPER)
    assert "name search" in rec.reason and tag not in rec.reason.lower()
    for params in ({"status": "DELETED"}, {"q": "x" * 101}, {"q": ""}):
        assert client.get("/api/platform/organisations", params=params, headers=H(SB_SUPER)).status_code == 422, params


# ------------------------------------------------------------------------------------------------ Team & Access
def test_identity_directory_for_role_administrators_with_typed_filters(client, world):
    from app.db import AdminSession
    from app.iam import is_current
    from app.models import RoleAssignment
    rows = client.get("/api/actors", headers=H(SB_TSA))
    assert rows.status_code == 200
    rows = rows.json()
    assert rows and {r["tenant_id"] for r in rows} == {TENANT}
    assert {"actor_id", "display_name", "email", "actor_type", "roles", "authority_basis", "tenant_name"} <= set(rows[0])
    unfiltered = client.get("/api/actors", headers=H("actor-cco")).json()
    assert sorted(r["actor_id"] for r in unfiltered) == sorted(r["actor_id"] for r in rows)  # same list, either reader

    agents = client.get("/api/actors", params={"actor_type": "AGENT"}, headers=H(SB_TSA)).json()
    assert agents and {r["actor_type"] for r in agents} == {"AGENT"}
    with AdminSession() as db:
        cco_holders = {a.principal_id for a in db.execute(select(RoleAssignment).where(
            RoleAssignment.tenant_id == TENANT, RoleAssignment.role_code == "CCO")).scalars() if is_current(a)}
    ccos = client.get("/api/actors", params={"role": "CCO"}, headers=H(SB_TSA)).json()
    assert {r["actor_id"] for r in ccos} == cco_holders and "actor-cco" in cco_holders
    named = client.get("/api/actors", params={"q": "PROBE SB-T"}, headers=H(SB_TSA)).json()
    assert [r["actor_id"] for r in named] == [SB_TSA]
    by_email = client.get("/api/actors", params={"q": "sb-owner@SIDEBAR"}, headers=H(SB_TSA)).json()
    assert [r["actor_id"] for r in by_email] == [SB_OWNER]
    # Wildcard characters are matched literally, not as patterns.
    assert client.get("/api/actors", params={"q": "%"}, headers=H(SB_TSA)).json() == []
    for params in ({"actor_type": "ROBOT"}, {"role": "cco"}, {"q": "x" * 101}):
        assert client.get("/api/actors", params=params, headers=H(SB_TSA)).status_code == 422, params
    # Another organisation's security administrator sees only its own people; auditors get no directory.
    assert {r["tenant_id"] for r in client.get("/api/actors", headers=H(B_TSA)).json()} == {TB}
    assert client.get("/api/actors", headers=H("actor-ext-auditor")).status_code == 403
    staff = client.get("/api/actors", params={"role": "SUPER_ADMIN"}, headers=H(SB_SUPER)).json()
    assert SB_SUPER in {r["actor_id"] for r in staff} and {r["tenant_id"] for r in staff} == {None}


def test_role_registry_endpoints_the_team_screen_uses(client, world):
    """The shapes the Team & Access screen reads (consumer contract) and its refusals."""
    roles = client.get("/api/roles", headers=H(A_TSA)).json()
    assert roles["policy_version"] and {"role_code", "permissions", "assignable", "scope_types"} <= set(roles["roles"][0])
    # The scopes it can assign at: its own organisation's legal entities only, identity fields only.
    assert roles["tenant_id"] == TA and f"entity-{TA}" in {e["entity_id"] for e in roles["entities"]}
    assert f"entity-{TB}" not in {e["entity_id"] for e in roles["entities"]}
    assert all(set(e) == {"entity_id", "legal_name", "country"} for e in roles["entities"])
    r = client.post("/api/role-assignments", headers=H(A_TSA), json={
        "principal_id": A_USER2, "role_code": "RISK_APPROVER", "scope_type": "LEGAL_ENTITY", "scope_id": f"entity-{TA}",
        "reason": "sidebar probe"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert {"assignment_id", "principal_id", "role_code", "scope_type", "scope_id", "valid_from", "valid_to",
            "in_force", "permissions", "status"} <= set(a)
    listed = client.get("/api/role-assignments", params={"principal_id": A_USER2}, headers=H(A_TSA)).json()
    assert a["assignment_id"] in {x["assignment_id"] for x in listed}
    access = client.get(f"/api/principals/{A_USER2}/effective-access", headers=H(A_TSA)).json()
    assert f"entity-{TA}" in access["entity_permissions"]
    assert client.post(f"/api/role-assignments/{a['assignment_id']}/revoke", headers=H(A_TSA),
                       json={"reason": "sidebar probe done"}).status_code == 200
    events = client.get(f"/api/role-assignments/{a['assignment_id']}/events", headers=H(A_TSA)).json()
    assert [e["event_type"] for e in events] == ["ASSIGNED", "REVOKED"]
    # Cross-tenant: organisation B's security admin cannot see or change organisation A's registry.
    assert client.get(f"/api/principals/{A_USER2}/effective-access", headers=H(B_TSA)).status_code == 404
    assert client.get(f"/api/role-assignments/{a['assignment_id']}/events", headers=H(B_TSA)).status_code == 404
    assert client.get("/api/roles", headers=H(A_USER)).status_code == 403
    assert client.get("/api/roles").status_code == 401


def test_sessions_are_revoked_by_their_principal_or_its_security_admin(client, world):
    """ZA-IAM-001 §23 POST /sessions/{id}/revoke ("self or authorized security admin; audit event"), IAM-REQ-015."""
    t1, t2 = _login(client, A_USER), _login(client, A_USER)
    mine = client.get("/api/sessions", headers=_bearer(t1))
    assert mine.status_code == 200
    mine = mine.json()
    assert len(mine) >= 2 and sum(s["current"] for s in mine) == 1
    assert {"session_id", "created_at", "expires_at", "user_agent", "current"} <= set(mine[0])
    assert not any("token" in k for s in mine for k in s)  # never the token or its hash
    current = next(s["session_id"] for s in mine if s["current"])
    other = next(s for s in client.get("/api/sessions", headers=_bearer(t2)).json() if s["current"])["session_id"]

    assert client.post(f"/api/sessions/{current}/revoke", headers=_bearer(t1), json={}).status_code == 409
    assert client.post(f"/api/sessions/{other}/revoke", headers=_bearer(t1), json={}).status_code == 200
    assert client.get("/api/me", headers=_bearer(t2)).status_code == 401          # takes effect at once
    assert client.post(f"/api/sessions/{other}/revoke", headers=_bearer(t1), json={}).status_code == 409
    own = _latest_audit("auth.session_revoked", object_id=other)
    assert own.actor_id == A_USER and own.tenant_id == TA and own.new_state == "REVOKED"

    # The organisation's security administrator lists and revokes a member's session, with a reason.
    t3 = _login(client, A_USER)
    t3_id = next(s for s in client.get("/api/sessions", headers=_bearer(t3)).json() if s["current"])["session_id"]
    listed = client.get(f"/api/principals/{A_USER}/sessions", headers=H(A_TSA))
    assert listed.status_code == 200 and t3_id in {s["session_id"] for s in listed.json()}
    assert other not in {s["session_id"] for s in listed.json()}
    assert client.post(f"/api/sessions/{t3_id}/revoke", headers=H(A_TSA), json={}).status_code == 422
    assert client.post(f"/api/sessions/{t3_id}/revoke", headers=H(A_TSA), json={"extra": 1}).status_code == 422
    r = client.post(f"/api/sessions/{t3_id}/revoke", headers=H(A_TSA), json={"reason": "lost laptop"})
    assert r.status_code == 200 and r.json()["session_id"] == t3_id
    assert client.get("/api/me", headers=_bearer(t3)).status_code == 401
    rec = _latest_audit("auth.session_revoked", object_id=t3_id)
    assert rec.actor_id == A_TSA and rec.tenant_id == TA and "lost laptop" in rec.reason and "security administrator" in rec.reason

    # Everyone else is refused: a colleague without the permission (403, recorded), another organisation's security
    # admin (404, as if it did not exist), the Super Admin (403), and anonymous callers (401). t1 survives all of it.
    before = denials(A_USER2)
    assert client.post(f"/api/sessions/{current}/revoke", headers=H(A_USER2), json={"reason": "probe"}).status_code == 403
    assert denials(A_USER2) - before == 1
    assert client.get(f"/api/principals/{A_USER}/sessions", headers=H(A_USER2)).status_code == 403
    assert client.get(f"/api/principals/{A_USER}/sessions", headers=H(B_TSA)).status_code == 404
    assert client.post(f"/api/sessions/{current}/revoke", headers=H(B_TSA), json={"reason": "probe"}).status_code == 404
    assert client.post(f"/api/sessions/{current}/revoke", headers=H(SB_SUPER), json={"reason": "probe"}).status_code == 403
    assert client.post("/api/sessions/no-such-session/revoke", headers=H(A_TSA), json={"reason": "probe"}).status_code == 404
    assert client.get("/api/sessions").status_code == 401
    assert client.post(f"/api/sessions/{current}/revoke", json={}).status_code == 401
    assert client.get("/api/me", headers=_bearer(t1)).status_code == 200


# ------------------------------------------------------------------------------------------------ Audit Trail
def _seed_audit(n: int, action="probe.listed", tenant=TA) -> list[str]:
    from app.db import AdminSession
    from app.models import AuditEvent
    from app.platform import audit, next_record_seq
    with AdminSession() as db:
        shared = next_record_seq(db)  # two records on one sequence number: the id breaks the tie
        for i in range(n):
            audit(db, action=action, object_type="Probe", object_id=f"probe-{secrets.token_hex(4)}", actor=A_USER,
                  tenant_id=tenant, reason=f"probe {i}", record_seq=shared if i < 2 else None)
        db.commit()
        return [e.id for e in db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant,
                                                                   AuditEvent.action == action)).scalars()]


def test_audit_trail_pages_through_a_pinned_stable_order(client, world):
    from app.db import AdminSession
    from app.models import AuditEvent
    action = f"probe.page{secrets.token_hex(3)}"
    ids = _seed_audit(7, action=action)
    seen, cursor, pages = [], None, 0
    first = client.get("/api/audit-events", params={"action": action, "limit": 3}, headers=H(A_USER)).json()
    assert set(first) == {"items", "page"} and {"next_cursor", "has_more", "watermark", "limit"} <= set(first["page"])
    pinned = first["page"]["watermark"]["record_seq"]
    _seed_audit(1, action=action)  # recorded after the listing started: not on its later pages
    body = first
    while True:
        pages += 1
        seen += body["items"]
        if not body["page"]["has_more"]:
            assert body["page"]["next_cursor"] is None
            break
        cursor = body["page"]["next_cursor"]
        assert body["page"]["watermark"]["record_seq"] == pinned
        body = client.get("/api/audit-events", params={"action": action, "limit": 3, "cursor": cursor},
                          headers=H(A_USER)).json()
    assert pages == 3 and sorted(e["id"] for e in seen) == sorted(ids)  # no duplicates, no gaps, nothing newer
    keys = [(e["record_seq"], e["id"]) for e in seen]
    assert keys == sorted(keys, reverse=True) and len(set(keys)) == len(keys)
    fresh = client.get("/api/audit-events", params={"action": action, "limit": 50}, headers=H(A_USER)).json()
    assert len(fresh["items"]) == 8
    with AdminSession() as db:
        assert all(db.get(AuditEvent, e["id"]).tenant_id == TA for e in seen)


def test_audit_trail_cursors_are_bound_to_scope_query_and_time(client, world, monkeypatch):
    action = f"probe.cursor{secrets.token_hex(3)}"
    _seed_audit(4, action=action)
    cur = client.get("/api/audit-events", params={"action": action, "limit": 2}, headers=H(A_USER)).json()["page"]["next_cursor"]
    assert cur

    def bad(params, actor=A_USER):
        r = client.get("/api/audit-events", params=params, headers=H(actor))
        return r.status_code, (r.json()["detail"] or {}).get("error") if r.status_code == 400 else None

    body, sig = cur.split(".")
    tampered = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    assert bad({"action": action, "limit": 2, "cursor": tampered}) == (400, "invalid_cursor")
    assert bad({"action": action, "limit": 2, "cursor": "not-a-cursor"}) == (400, "invalid_cursor")
    assert bad({"action": action, "limit": 3, "cursor": cur}) == (400, "invalid_cursor")           # other page size
    assert bad({"category": "probe", "limit": 2, "cursor": cur}) == (400, "invalid_cursor")       # other filter
    assert bad({"action": action, "limit": 2, "cursor": cur}, actor=B_USER) == (400, "invalid_cursor")  # other tenant
    import app.pagination as pagination
    monkeypatch.setattr(pagination, "CURSOR_TTL_SECONDS", -1)
    stale = client.get("/api/audit-events", params={"action": action, "limit": 2}, headers=H(A_USER)).json()["page"]["next_cursor"]
    monkeypatch.undo()
    assert bad({"action": action, "limit": 2, "cursor": stale}) == (400, "cursor_expired")
    assert client.get("/api/audit-events", params={"action": action, "limit": 2, "cursor": cur},
                      headers=H(A_USER)).status_code == 200


def test_audit_trail_filters_validation_and_authorization(client, world):
    tag = secrets.token_hex(3)
    _seed_audit(3, action=f"probe.a{tag}")
    _seed_audit(2, action=f"probe.b{tag}")

    def items(**params):
        r = client.get("/api/audit-events", params={"limit": 200, **params}, headers=H(A_USER))
        assert r.status_code == 200, r.text
        return r.json()["items"]

    assert len(items(action=f"probe.a{tag}")) == 3
    cat = items(category="probe")
    assert {e["action"] for e in cat} >= {f"probe.a{tag}", f"probe.b{tag}"} and all(e["action"].startswith("probe.") for e in cat)
    assert all(e["actor_id"] == A_USER for e in items(actor_id=A_USER))
    obj = items(action=f"probe.a{tag}")[0]["object_id"]
    assert [e["object_id"] for e in items(object_type="Probe", object_id=obj)] == [obj]
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert items(action=f"probe.a{tag}", recorded_from=future) == []
    assert len(items(action=f"probe.a{tag}", recorded_to=future)) == 3
    # The category filter is a literal prefix: "_" and "%" are not wildcards.
    assert items(category="prob_") == []
    for params in ({"limit": 0}, {"limit": 201}, {"category": "Probe"}, {"action": "DROP TABLE"},
                   {"object_type": "x;y"}, {"recorded_from": future, "recorded_to": future}, {"recorded_from": "yesterday"}):
        assert client.get("/api/audit-events", params=params, headers=H(A_USER)).status_code == 422, params
    # Organisation B sees none of A's records; role administrators, auditors and platform staff have no audit read.
    assert all(f"probe.a{tag}" != e["action"] for e in client.get(
        "/api/audit-events", params={"category": "probe", "limit": 200}, headers=H(B_USER)).json()["items"])
    for actor in (A_TSA, "actor-ext-auditor", SB_SUPER):
        assert client.get("/api/audit-events", headers=H(actor)).status_code == 403, actor
    assert client.get("/api/audit-events").status_code == 401
    # The legacy list is unchanged for its consumers; a non-positive limit is refused instead of reaching the database.
    assert isinstance(client.get("/api/audit", headers=H(A_USER)).json(), list)
    assert client.get("/api/audit", params={"limit": -1}, headers=H(A_USER)).status_code == 422


# ------------------------------------------------------------------------------------------------ Org Settings
def test_entity_registration_is_validated_and_audited(client, world):
    for body in ({"legal_name": "Probe Ltd", "country": "GBR"}, {"legal_name": "Probe Ltd", "country": "G1"},
                 {"legal_name": " ", "country": "GB"}, {"legal_name": "x" * 201, "country": "GB"},
                 {"legal_name": "Probe Ltd", "country": "GB", "registration_ref": "r" * 101},
                 {"legal_name": "Probe Ltd", "country": "GB", "home_cell_id": "uk-south-cell-b"}, {"country": "GB"}):
        assert client.post("/api/org/entities", headers=H(A_OWNER), json=body).status_code == 422, body
    assert client.post("/api/org/entities", headers=H(A_USER), json={"legal_name": "Probe Ltd", "country": "GB"}).status_code == 403
    assert client.post("/api/org/entities", json={"legal_name": "Probe Ltd", "country": "GB"}).status_code == 401
    r = client.post("/api/org/entities", headers=H(A_OWNER), json={"legal_name": "  Probe Second Entity Ltd ", "country": "ie"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["legal_name"] == "Probe Second Entity Ltd" and e["country"] == "IE" and e["tenant_id"] == TA
    rec = _latest_audit("entity.registered", object_id=e["entity_id"])
    assert rec.actor_id == A_OWNER and rec.tenant_id == TA and rec.new_state == "ACTIVE"
    assert e["entity_id"] in {x["entity_id"] for x in client.get("/api/org/tenant", headers=H(A_USER)).json()["entities"]}
    assert e["entity_id"] not in client.get("/api/org/tenant", headers=H(B_USER)).text
    # Subjects: lengths are bounded, and another organisation's entity is not found.
    assert client.post("/api/org/subjects", headers=H(A_USER), json={
        "entity_id": f"entity-{TA}", "subject_type": "BUSINESS_UNIT", "display_name": "x" * 201}).status_code == 422
    assert client.post("/api/org/subjects", headers=H(A_USER), json={
        "entity_id": f"entity-{TB}", "subject_type": "BUSINESS_UNIT", "display_name": "Probe unit"}).status_code == 404


# ------------------------------------------------------------------------------------------------ Overview
def test_overview_obligation_occurrence_figures_are_recorded_counts(client, world):
    from app.db import AdminSession
    from app.models import ObligationInstance, ObligationOccurrence
    d = client.get("/api/dashboard", headers=H(A_USER)).json()["obligation_occurrences"]
    assert (d["unfulfilled"], d["overdue"]) == (2, 1)  # the WITHDRAWN obligation's occurrence and the fulfilled one are out
    # The seeded organisation: the same figures recounted straight from its records.
    now = datetime.now(timezone.utc)
    with AdminSession() as db:
        adopted = {o.lineage_id for o in db.execute(select(ObligationInstance).where(
            ObligationInstance.tenant_id == TENANT, ObligationInstance.record_to_seq.is_(None),
            ObligationInstance.lifecycle == "ADOPTED")).scalars()}
        open_ = [o for o in db.execute(select(ObligationOccurrence).where(
            ObligationOccurrence.tenant_id == TENANT, ObligationOccurrence.fulfilled_at.is_(None))).scalars()
            if o.obligation_lineage_id in adopted]
    from app.platform import ensure_utc
    seeded = client.get("/api/dashboard", headers=H("actor-cco")).json()["obligation_occurrences"]
    assert seeded["unfulfilled"] == len(open_)
    assert seeded["overdue"] == sum(1 for o in open_ if ensure_utc(o.due_at) <= now)
    assert client.get("/api/dashboard", headers=H(B_USER)).json()["obligation_occurrences"] == {
        **seeded, "unfulfilled": 0, "overdue": 0}


if __name__ == "__main__":  # regenerate the `perms` of frontend/lib/screen-access.json (run from backend/)
    import os
    import sys
    import tempfile
    os.environ.update(ZA_ALLOW_SQLITE="1", ZA_SKIP_DOTENV="1", ZA_DATA_DIR=tempfile.mkdtemp(), ZA_ENVIRONMENT="test")
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    doc = json.loads(ACCESS.read_text(encoding="utf-8"))
    for screen in [*doc["screens"].values(), *doc["shell"].values(), *doc["panels"].values()]:
        screen["perms"] = screen_perms(screen["reads"])
    doc["roles"] = tenant_role_permissions()
    ACCESS.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {ACCESS} ({len(doc['screens'])} screens, {len(doc['shell'])} shell reads, {len(doc['panels'])} panels)")
