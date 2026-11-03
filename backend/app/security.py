"""Request context, tenant scoping and authorization (ZA-IAM-001 §06, DAT-REQ-005; roadmap Stage 4).

Identity: a signed session token (``Authorization: Bearer``) issued by ``app/auth.py``.
The ``X-Actor-Id`` development header is accepted ONLY when ``ZA_DEV_IDENTITY_HEADER=1``
(used by the test-suite and for agent/service simulation); it is off by default. Every tenant-scoped query MUST go through
``Ctx.tenant`` so cross-tenant reads are impossible from the application layer;
PostgreSQL RLS (migrations/versions/0002_*, 0007) enforces the same predicate below it.

Authorization (Stage 4): one decision, ``Ctx.check``, for every route, reads included. Authority comes from the scoped
role registry (``iam_role_assignment``, app/iam.py), read on every request: principal + role + explicit scope (TENANT,
LEGAL_ENTITY, PLATFORM) + validity window (IAM-REQ-007, IAM-REQ-010, IAM-REQ-015). Every route declares an operation and
its requirement (``authorize``/``authenticated``) or is explicitly public, and the API refuses to start otherwise
(API-REQ-031; ``unguarded_operations``).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .db import SessionLocal, capability, get_db, set_principal, set_system_scope
from .models import Actor, AuthCredential, AuthSession, RoleAssignment
from .platform import audit, ensure_utc

log = logging.getLogger(__name__)

# Role catalogue -> permitted actions (separation of duties; role names are not authority by themselves).
# request_exception = ask for a governed departure (exception or compensating control); approving it is separate.
# assurance_review = governed review decisions on evidence conflicts and IPE anomalies (ZA-IAM-001 §08 ASSURANCE_REVIEWER).
# reevaluate_ipe = re-run a source run's IPE checks under the policy the run was executed with (never another one).
# Read classes (Stage 4a): "read" is the organisation's workforce read of its own records; "audit_read" is the
# ZA-IAM-001 §14 external-auditor resource allowlist (packages, evidence, control/obligation dossiers, reports), which
# only the routes in that allowlist accept. Read permissions are separate from every mutation permission.
# ZA-IAM-001 §08/§14: read selected evidence/controls/packages, verification, no tenant admin/business mutations, and no
# workforce-wide "read" (audit log, outbox, people directory, facts, drafts and AI assets stay out of reach).
EXTERNAL_AUDITOR_PERMISSIONS = {"audit_read", "verify"}
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "CCO": {"read", "attest", "approve_exception", "request_evaluation", "manage_facts", "challenge", "package",
            "manage_issues", "request_exception", "assurance_review"},
    "LEGAL_REVIEWER": {"read", "attest", "challenge", "request_evaluation", "legal_disposition"},
    "REGULATORY_ANALYST": {"read", "manage_facts", "request_evaluation", "challenge"},
    "CONTROL_OWNER": {"read", "submit_evidence", "manage_controls", "manage_issues", "request_exception"},
    "INTERNAL_AUDIT": {"read", "package", "verify", "reevaluate_ipe"},
    "AI_GOVERNANCE_LEAD": {"read", "manage_ai", "manage_facts", "request_evaluation", "grant_authority",
                           "request_policy_decision"},
    # manage_connectors covers this organisation's connector installations only. The connector certification registry
    # is shared by every organisation and needs certify_connectors (Zoiko platform staff), which no tenant role holds.
    "PLATFORM_ADMIN": {"read", "manage_connectors", "manage_tenant", "checkpoint", "reevaluate_ipe"},
    # Organisation Admin = ZA-IAM-001 §08 role template TENANT_OWNER: organisation administration within its own tenant;
    # no attest/approve rights, so it cannot bypass SoD or attest legal determinations. It "may delegate admin"
    # (delegate_admin): it alone appoints or removes TENANT_SECURITY_ADMIN (app/iam.py ASSIGNABILITY), never itself.
    "TENANT_OWNER": {"read", "manage_tenant", "delegate_admin"},
    # ZA-IAM-001 §08 TENANT_SECURITY_ADMIN: role/grant management and access review for its organisation. No business
    # read or mutation: it administers access, not the regulatory record (Stage 4b).
    "TENANT_SECURITY_ADMIN": {"manage_role_assignments"},
    "RISK_APPROVER": {"read", "approve_exception", "approve_compensation", "assurance_review"},
    "EXTERNAL_AUDITOR": EXTERNAL_AUDITOR_PERMISSIONS,
    # Zoiko content operations (tenant_id NULL): no standing customer-evidence access (INV-14)
    "CONTENT_ANALYST": {"content_author"},
    "ENCODING_ENGINEER": {"content_author", "content_peer_review"},
    "CONTENT_LEGAL_REVIEWER": {"content_legal_review"},
    "CONTENT_RELEASE_MANAGER": {"content_release"},
    # Zoiko platform Super Admin (tenant_id NULL): the customer-organisation registry only. No tenant-data permission,
    # so evidence/facts/assurance stay out of reach (INV-14, IAM-REQ-024). Created only by the one-time .env bootstrap
    # (app/accounts.py); no API grants it (WF-IAM-01: no "make super admin" button).
    "SUPER_ADMIN": {"manage_organisations"},
    # Zoiko platform staff (tenant_id NULL) who record connector certification in the global registry (FR-CON-001/002,
    # INV-11). Like SUPER_ADMIN, no API grants this role.
    "CONNECTOR_CERTIFICATION_MANAGER": {"certify_connectors"},
    "SERVICE": {"read", "submit_evidence", "request_evaluation", "manage_facts", "checkpoint", "request_policy_decision"},
}

# Requirement shorthands the routers use (read classes, Stage 4a).
READ = ("read",)                                  # the organisation's own records
DOSSIER_READ = ("read", "audit_read")             # also the ZA-IAM-001 §14 external-auditor allowlist
CONTENT_READERS = ("content_author", "content_peer_review", "content_legal_review", "content_release")
PLATFORM_READERS = ("manage_organisations", "certify_connectors")


@dataclass(frozen=True)
class Requirement:
    """What a protected action needs: one of `permissions`, a named HUMAN if `human`, a Zoiko platform principal
    (tenant_id NULL) if `platform`. `self_service`: any signed-in principal acting on its own identity only."""
    permissions: tuple[str, ...]
    human: bool = False
    platform: bool = False
    self_service: bool = False


@dataclass(frozen=True)
class EntityOf:
    """How a route's resource resolves to one legal entity, server-side, from the identifiers in its request
    (IAM-REQ-009). Lets an entity-scoped role (IAM-REQ-010) reach that resource and nothing else.
    `find(db, tenant_id, params)` returns the row only if it belongs to `tenant_id`; `entity(db, row)` its entity."""
    label: str
    find: Callable[[Session, str, dict], Any]
    entity: Callable[[Session, Any], str | None] = lambda _db, row: getattr(row, "entity_id", None)


def entity_of(model, param: str, label: str, entity: Callable[[Session, Any], str | None] | None = None) -> EntityOf:
    """The common case: `model` is looked up by its primary key from request parameter `param`."""
    def find(db: Session, tenant_id: str, params: dict):
        key = params.get(param)
        row = db.get(model, key) if key else None
        return row if row is not None and getattr(row, "tenant_id", None) == tenant_id else None
    return EntityOf(label, find, entity or (lambda _db, row: getattr(row, "entity_id", None)))


@dataclass
class Ctx:
    db: Session
    actor: Actor
    route: str | None = None  # "METHOD /api/path/{template}" of the request; recorded with a refusal
    _grants: list | None = field(default=None, repr=False)
    authority: tuple[str, ...] = ()  # the role assignments that authorised this request (IAM-REQ-037)

    @property
    def tenant(self) -> str:
        if not self.actor.tenant_id:
            raise self.deny("NO_TENANT_SCOPE", "Content/platform staff have no standing tenant data access (INV-14).")
        return self.actor.tenant_id

    def grants(self) -> list[RoleAssignment]:
        """The principal's role assignments in force now, read from the registry once per request."""
        if self._grants is None:
            from .iam import active_assignments
            self._grants = active_assignments(self.db, self.actor)
        return self._grants

    def perms(self, entity_id: str | None = None) -> set[str]:
        """Permissions in force for a request whose resource is `entity_id` (None: organisation-/platform-wide only)."""
        from .iam import applies
        out: set[str] = set()
        for a in self.grants():
            if applies(a, entity_id):
                out |= ROLE_PERMISSIONS.get(a.role_code, set())
        return out

    def _conferring(self, wanted: set[str], entity_id: str | None) -> list[str]:
        from .iam import applies
        return [a.assignment_id for a in self.grants()
                if applies(a, entity_id) and ROLE_PERMISSIONS.get(a.role_code, set()) & wanted]

    def require(self, perm: str) -> None:
        if perm not in self.perms():
            raise self.deny("MISSING_PERMISSION", f"Actor {self.actor.display_name} lacks permission '{perm}'.")

    def require_human(self, what: str) -> None:
        """INV-01: AI/agent/service actors can never be the accountable authorizer."""
        if self.actor.actor_type != "HUMAN":
            raise self.deny("HUMAN_REQUIRED", f"{what} requires a named HUMAN actor (INV-01); got {self.actor.actor_type}.")

    def check(self, req: Requirement, resource: EntityOf | None = None, params: dict | None = None) -> None:
        """The single authorization decision, used by route guards and by service functions that must not be callable
        around their route. Deny by default (IAM-REQ-007): principal type, platform/tenant boundary, then a permission
        conferred by a role assignment in force whose scope covers the request. An entity-scoped assignment counts only
        when `resource` resolves server-side to its entity; a resource outside that scope is answered exactly like one
        that does not exist (404), so a denial never reveals another entity's records (API-001 existence masking)."""
        if self.actor.actor_type == "AGENT":
            # FR-AI-004 / ZA-DOM-001 §10: an agent may act only under an explicit, current AuthorityGrant for the exact
            # action and scope. Agent sign-in identities are not yet bound to the agent subjects that grants name, so
            # no grant can be matched: refuse rather than let role membership or a valid identity stand in for one.
            raise self.deny("AGENT_WITHOUT_GRANT",
                            "AGENT actors cannot perform this action: it needs a matching AuthorityGrant bound "
                            "to the agent's identity (INV-01, FR-AI-004), and agent identities are not yet "
                            "bound to grants.")
        if req.platform and self.actor.tenant_id is not None:
            raise self.deny("PLATFORM_FUNCTION", "This is a Zoiko platform function; organisation roles cannot perform it.")
        if req.human:
            self.require_human("This action")
        if req.self_service:
            return
        wanted = set(req.permissions)
        conferring = self._conferring(wanted, None)
        if not conferring and resource is not None and self.actor.tenant_id and any(
                a.scope_type == "LEGAL_ENTITY" and ROLE_PERMISSIONS.get(a.role_code, set()) & wanted
                for a in self.grants()):
            row = resource.find(self.db, self.actor.tenant_id, params or {})
            if row is None:
                raise HTTPException(404, f"{resource.label} not found")
            conferring = self._conferring(wanted, resource.entity(self.db, row))
            if not conferring:
                raise self.deny("ENTITY_OUT_OF_SCOPE", f"Actor {self.actor.display_name}'s entity-scoped roles do not "
                                f"cover this {resource.label}.", status=404, public=f"{resource.label} not found")
        if not conferring:
            needed = " or ".join(f"'{p}'" for p in req.permissions)
            raise self.deny("MISSING_PERMISSION", f"Actor {self.actor.display_name} lacks permission {needed}.")
        self.authority = tuple(dict.fromkeys(self.authority + tuple(conferring)))

    def deny(self, reason_code: str, message: str, *, status: int = 403, public: str | None = None) -> HTTPException:
        """Record ZA-IAM-001 §21 ``authorization.denied`` and return the refusal to raise (IAM-REQ-037, SEC-REQ-031).
        `status`/`public`: what the caller sees when the denial must not reveal that the resource exists (404).

        The refused request fails, so its transaction is rolled back first and nothing it may have written survives;
        the record then commits on its own, in the same session and role, under the actor's own tenant scope (platform
        staff: none), so an organisation's audit trail shows refusals of its own people. Not inside a SAVEPOINT, whose
        rollback the caller still owns: there the refusal stands unrecorded. If the record cannot be written the
        refusal still stands (IAM-REQ-042: an infrastructure failure never becomes an ALLOW)."""
        db = self.db
        try:
            if db.in_nested_transaction():
                log.warning("authorization.denied for actor %s not recorded: refused inside a savepoint", self.actor.actor_id)
            else:
                db.rollback()
                db.info.pop("authority", None)  # a refusal record is not the refused action
                audit(db, action="authorization.denied", object_type="Endpoint", object_id=(self.route or "-")[:64],
                      actor=self.actor, tenant_id=self.actor.tenant_id, new=reason_code, reason=message)
                db.commit()
        except Exception:  # noqa: BLE001 - recording must never turn a refusal into anything else
            log.exception("could not record authorization.denied for actor %s", getattr(self.actor, "actor_id", "?"))
            db.rollback()
        return HTTPException(status, public or message)

    def actor_ref(self) -> dict:
        return {"actor_id": self.actor.actor_id, "actor_type": self.actor.actor_type, "name": self.actor.display_name}

    def scoped(self, obj, label: str = "object"):
        if obj is None or getattr(obj, "tenant_id", None) != self.tenant:
            raise HTTPException(404, f"{label} not found")
        return obj


DEV_HEADER = os.environ.get("ZA_DEV_IDENTITY_HEADER", "0") == "1"
# Roadmap 4d / audit I-08 (IAM-REQ-001, SEC-REQ-024): the unauthenticated X-Actor-Id header is a development and test
# convenience only. ZA_ENVIRONMENT is the deployment's environment name (set by .github/workflows/deploy-environment.yml);
# the header is honoured only where it names one of these, explicitly. Unset or anything else refuses to start.
DEV_HEADER_ENVIRONMENTS = frozenset({"development", "test"})


def dev_header_violation(environ=os.environ, enabled: bool | None = None) -> str | None:
    """Why the API must not start with the dev identity header, or None. Checked once at import by app/main.py."""
    if not (DEV_HEADER if enabled is None else enabled):
        return None
    env = (environ.get("ZA_ENVIRONMENT") or "").strip().lower()
    if env in DEV_HEADER_ENVIRONMENTS:
        return None
    return (f"Refusing to start: ZA_DEV_IDENTITY_HEADER=1 lets any caller act as any principal, and is allowed only when "
            f"ZA_ENVIRONMENT is one of {sorted(DEV_HEADER_ENVIRONMENTS)} (it is {env or 'unset'!r}).")


def get_ctx(request: Request, authorization: str | None = Header(default=None),
            x_actor_id: str | None = Header(default=None), db: Session = Depends(get_db)) -> Ctx:
    from .auth import bearer, resolve_token, tenant_active
    tok = bearer(authorization)
    if not tok and not (x_actor_id and DEV_HEADER):
        raise HTTPException(401, "Sign in required")
    # The identity tables are reachable only inside this block (signed `auth` capability, migration 0007); the handler
    # runs without it, under the principal's own tenant, actor and session.
    with capability(db, "auth"):
        if tok:
            actor, _ = resolve_token(db, tok)  # also refuses sessions of a suspended organisation
            db.info["revalidate_session"] = True  # this request's writes commit only while its session is live
        else:
            actor = db.get(Actor, x_actor_id)
            if actor is None:
                raise HTTPException(401, "Unknown actor")
            set_principal(db, actor.tenant_id, actor.actor_id)  # PostgreSQL RLS: this request sees only its tenant
        if not tenant_active(db, actor):
            raise HTTPException(403, "This organisation is suspended; access is disabled.")
        if tok:
            cred = db.get(AuthCredential, actor.actor_id)
            if cred is not None and cred.must_change:
                raise HTTPException(403, {"error": "PASSWORD_CHANGE_REQUIRED",
                                          "message": "Change your temporary password before continuing."})
    return Ctx(db=db, actor=actor, route=f"{request.method} {_route_template(request)}")


def _route_template(request: Request) -> str:
    """The request's path with its parameters left as placeholders ("/api/evidence/{evidence_id}"); the matched route
    knows its path below the router's mount prefix only, so the prefix is taken from the concrete URL."""
    path, route = request.url.path, request.scope.get("route")
    try:
        concrete = route.path_format.format(**request.path_params)
    except (AttributeError, KeyError, IndexError, ValueError):
        return path
    return path[:len(path) - len(concrete)] + route.path if path.endswith(concrete) else path


@event.listens_for(SessionLocal, "before_commit")
def _revalidate_session(db: Session) -> None:
    """IAM-REQ-014/015: a request's writes commit only while the session that authorised them is still live.

    The session row is read again immediately before commit with FOR SHARE (PostgreSQL), so a concurrent logout,
    password change or organisation suspension either commits first and this commit is refused, or waits until this
    transaction has committed. Requests authenticated by a session token only; nothing else is affected."""
    sid = db.info.get("auth_session_id")
    if sid is None or not db.info.get("revalidate_session"):
        return
    row = db.execute(select(AuthSession.revoked_at, AuthSession.expires_at)
                     .where(AuthSession.session_id == sid).with_for_update(read=True)).first()
    if row is None or row.revoked_at is not None or ensure_utc(row.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Session is no longer valid; sign in again")


@event.listens_for(SessionLocal, "before_commit")
def _revalidate_authority(db: Session) -> None:
    """IAM-REQ-014 / IAM-REQ-036: a state change commits only while a role assignment that authorised it is still in
    force. The assignments are read again immediately before commit with FOR SHARE (PostgreSQL); a revocation takes
    FOR UPDATE on the same row (app/iam.py), so it either commits first and this commit is refused, or waits for it.
    Set by `authorize` for state-changing routes of organisation principals (platform roles have no revocation path)."""
    ids = db.info.get("authority")
    if not ids:
        return
    from .iam import ACTIVE
    now = datetime.now(timezone.utc)
    rows = db.execute(select(RoleAssignment.status, RoleAssignment.valid_from, RoleAssignment.valid_to)
                      .where(RoleAssignment.assignment_id.in_(ids)).with_for_update(read=True)).all()
    if not any(r.status == ACTIVE and ensure_utc(r.valid_from) <= now
               and (r.valid_to is None or ensure_utc(r.valid_to) > now) for r in rows):
        raise HTTPException(403, "The role that authorised this action was revoked or has expired; nothing was saved.")


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def authorize(*permissions: str, human: bool = False, platform: bool = False, entity: EntityOf | None = None):
    """Route guard for every protected endpoint, reads included: `ctx: Ctx = Depends(authorize("perm"))`.

    It runs before the request body is validated, so an unauthorised caller gets 403 whatever it sends and nothing is
    written. `entity`: how the route's resource resolves to a legal entity, so that entity-scoped roles can reach it.
    Deny by default: `unguarded_operations` lists any route without a guard that is not explicitly public, and the API
    refuses to start while one exists (app/main.py)."""
    if not permissions:
        raise ValueError("authorize() needs at least one permission")
    req = Requirement(tuple(permissions), human, platform)

    def guard(request: Request, ctx: Ctx = Depends(get_ctx)) -> Ctx:
        ctx.check(req, entity, {**request.query_params, **request.path_params})
        if request.method in MUTATING_METHODS and ctx.actor.tenant_id and ctx.authority:
            ctx.db.info["authority"] = ctx.authority  # re-checked before commit (_revalidate_authority)
        return ctx

    guard.za_requirement = req
    guard.za_entity = entity
    return guard


def authenticated():
    """Guard for self-service routes that act only on the caller's own identity (e.g. ``GET /me``)."""
    req = Requirement((), self_service=True)

    def guard(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        ctx.check(req)
        return ctx

    guard.za_requirement = req
    guard.za_entity = None
    return guard


# State-changing routes that are deliberately not behind `authorize`, each with the reason.
PUBLIC_MUTATIONS = {
    "/api/auth/login": "the credential check is the authentication step itself",
    "/api/auth/logout": "revokes only the presented session",
    "/api/auth/password": "authenticates with the session token and the current password",
    "/api/packages/verify-upload": "public by design (ZA-EVD-001 §18 offline verification); touches no tenant data",
}
# Read routes that are deliberately not behind `authorize`, each with the reason (Stage 4a).
PUBLIC_READS = {
    "/api/health": "liveness probe; cell ID and dictionary version only",
    "/api/dictionary": "the controlled code dictionary (ZA-GLS-001); no tenant data",
    "/api/trust/public-key": "the package-signing public key, needed for offline verification (ZA-EVD-001 §18)",
    "/api/auth/session": "authenticates the presented session token itself and returns only its own principal",
    "/api/auth/demo-accounts": "demo mode only (404 otherwise); never returns a credential",
}


def route_requirement(route: APIRoute) -> Requirement | None:
    return next((d.call.za_requirement for d in route.dependant.dependencies if hasattr(d.call, "za_requirement")), None)


def route_entity(route: APIRoute) -> EntityOf | None:
    return next((d.call.za_entity for d in route.dependant.dependencies if hasattr(d.call, "za_entity")), None)


def unguarded_mutations(routers, prefix: str = "/api") -> list[str]:
    """State-changing routes that have no `authorize` guard and are not listed in PUBLIC_MUTATIONS."""
    out = []
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute) or not route.methods & MUTATING_METHODS:
                continue
            path = prefix + route.path
            if route_requirement(route) is None and path not in PUBLIC_MUTATIONS:
                out.append(f"{','.join(sorted(route.methods & MUTATING_METHODS))} {path}")
    return out


def _public_reason(method: str, path: str) -> str | None:
    return PUBLIC_MUTATIONS.get(path) if method in MUTATING_METHODS else PUBLIC_READS.get(path)


@dataclass(frozen=True)
class Operation:
    """One entry of the operation registry (API-REQ-031): a stable operationId, its route, and what it requires."""
    operation_id: str
    method: str
    path: str
    requirement: Requirement | None
    entity_scoped: bool
    public: str | None


def operation_id(route: APIRoute) -> str:
    """Stable operationId: the endpoint's module and function name, e.g. ``evidence.get_evidence``."""
    return f"{route.endpoint.__module__.rsplit('.', 1)[-1]}.{route.endpoint.__name__}"


def operations(routers, prefix: str = "/api") -> list[Operation]:
    out = []
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods):
                out.append(Operation(operation_id(route), method, prefix + route.path, route_requirement(route),
                                     route_entity(route) is not None, _public_reason(method, prefix + route.path)))
    return out


def unguarded_operations(routers, prefix: str = "/api") -> list[str]:
    """Every route, of any method, without a declared requirement that is not explicitly public (deny by default)."""
    return [f"{op.method} {op.path}" for op in operations(routers, prefix) if op.requirement is None and op.public is None]


def assign_operation_ids(routers) -> None:
    """Give every route its stable operationId (used by OpenAPI, the audit route record and tests). Duplicates refuse."""
    seen: dict[str, str] = {}
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute):
                oid = operation_id(route)
                if oid in seen and seen[oid] != route.path:
                    raise RuntimeError(f"Duplicate operationId {oid}: {seen[oid]} and {route.path}")
                seen[oid] = route.path
                route.operation_id = oid


@contextmanager
def system_scope(db: Session, reason: str):
    """Documented cell-wide platform job (bypasses tenant RLS for the enclosed work only).

    Allowed uses: rule-release impact re-evaluation, shadow impact (aggregate output only),
    cell ledger checkpoints, and the Super Admin organisation-registry read (org_tenant / org_legal_entity
    metadata only, app/routers/platform.py). Never wrap request handlers that return tenant data to a caller.
    """
    set_system_scope(db, True)
    try:
        yield
    finally:
        set_system_scope(db, False)
