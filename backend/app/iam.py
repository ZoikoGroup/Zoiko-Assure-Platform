"""Scoped role registry: role assignments, their history, and effective access (roadmap Stage 4b).

ZA-IAM-001 is the contract:

* §02 / §22 ``RoleAssignment`` — principal + role template + **scope** + valid time + approval/source; "Bitemporal;
  revocation creates successor/closure". Stored as ``iam_role_assignment`` (the current row; only its closure columns
  ever change, enforced by a database trigger) and ``iam_role_assignment_event`` (append-only history).
* IAM-REQ-010 / §08 role-template rule — every assignment has an explicit scope: ``TENANT`` (the principal's own
  organisation), ``LEGAL_ENTITY`` (one legal entity of it) or ``PLATFORM`` (Zoiko staff, no organisation). An
  entity-scoped role confers its permissions only on a resource the server resolves to that entity, never on a
  tenant-wide list (app/security.py ``Ctx.check``).
* IAM-INV-09 / §21 — every assignment and revocation is versioned history (event row) and a security audit record
  (``role.assigned`` / ``role.revoked``). ZA-EVT-001 has no role-assignment event type, so none is emitted.
* IAM-REQ-015 — authority is read from this registry on every request, so a revocation takes effect at the next
  authorisation decision; a mutation already authorised re-reads its authorising assignments before it commits
  (IAM-REQ-014/036; app/security.py ``_revalidate_authority``).
* IAM-REQ-021 / §10 — a principal never assigns a role to itself. §08: role/grant management belongs to
  TENANT_SECURITY_ADMIN (permission ``manage_role_assignments``); TENANT_OWNER "may delegate admin" (permission
  ``delegate_admin``), so it alone appoints TENANT_SECURITY_ADMIN.
* IAM-REQ-027 / §14 — external auditors are engagement principals, not workforce members: EXTERNAL_AUDITOR is never
  assigned through this API (the engagement model is not built yet), and nobody else may hold it.

``org_actor.roles`` is no longer authoritative. It is kept as a cache of each principal's current tenant- or
platform-wide roles, written in the same transaction as every change, so a downgrade of migration 0009 restores the
previous behaviour with the latest roles; entity-scoped roles are left out of it (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Actor, LegalEntity, RoleAssignment, RoleAssignmentEvent
from .platform import audit, ensure_utc, next_record_seq

TENANT, LEGAL_ENTITY, PLATFORM = "TENANT", "LEGAL_ENTITY", "PLATFORM"
ACTIVE, REVOKED = "ACTIVE", "REVOKED"
ASSIGNED_EVENT, REVOKED_EVENT = "ASSIGNED", "REVOKED"
# Identifies the role catalogue and assignment rules this code enforces; recorded with every assignment change
# (IAM-REQ-037 "policy version"). Change it whenever ROLE_PERMISSIONS or ASSIGNABILITY changes.
AUTHZ_POLICY_VERSION = "za-authz-2026-11-02"
# A new assignment may start now or later, never earlier: a back-dated start would claim authority that was not held.
CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class Assignability:
    """Who may hold a role through this registry's API, at which scopes, and who may assign or revoke it."""
    principal_types: frozenset[str]
    scope_types: frozenset[str]
    by_permission: str                # the grantor holds this permission organisation-wide
    assignable: bool = True           # False: revocation only (the role is granted through another governed path)


_BUSINESS = Assignability(frozenset({"HUMAN"}), frozenset({TENANT, LEGAL_ENTITY}), by_permission="manage_role_assignments")
# The tenant roles this API manages. TENANT_OWNER is appointed by the Zoiko Super Admin (app/routers/platform.py);
# platform roles (SUPER_ADMIN, content operations, connector certification) are never granted through any API.
ASSIGNABILITY: dict[str, Assignability] = {
    "CCO": _BUSINESS, "LEGAL_REVIEWER": _BUSINESS, "REGULATORY_ANALYST": _BUSINESS, "CONTROL_OWNER": _BUSINESS,
    "INTERNAL_AUDIT": _BUSINESS, "AI_GOVERNANCE_LEAD": _BUSINESS, "PLATFORM_ADMIN": _BUSINESS, "RISK_APPROVER": _BUSINESS,
    # IAM §08 "Tenant / selected entities"; this stage enforces role management at tenant scope only.
    # IAM §08: TENANT_OWNER "may delegate admin" (permission delegate_admin, held by TENANT_OWNER only).
    "TENANT_SECURITY_ADMIN": Assignability(frozenset({"HUMAN"}), frozenset({TENANT}), by_permission="delegate_admin"),
    # Revocable by a security admin (IAM §24: "security admin can revoke locally in emergency"), never assignable here:
    # external engagements (IAM §14, IAM-REQ-027) and workload identities (IAM §15) are separate, unbuilt paths.
    "EXTERNAL_AUDITOR": Assignability(frozenset({"EXTERNAL_AUDITOR"}), frozenset({TENANT}),
                                      by_permission="manage_role_assignments", assignable=False),
    "SERVICE": Assignability(frozenset({"SERVICE"}), frozenset({TENANT}), by_permission="manage_role_assignments",
                             assignable=False),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_current(a: RoleAssignment, at: datetime | None = None) -> bool:
    at = at or _now()
    return (a.status == ACTIVE and ensure_utc(a.valid_from) <= at
            and (a.valid_to is None or ensure_utc(a.valid_to) > at))


def active_assignments(db: Session, actor: Actor, at: datetime | None = None) -> list[RoleAssignment]:
    """The principal's assignments in force at `at` (default now), within its own organisation only: an assignment
    recorded under another tenant never confers anything (TEN isolation; RLS hides it as well)."""
    at = at or _now()
    q = select(RoleAssignment).where(RoleAssignment.principal_id == actor.actor_id, RoleAssignment.status == ACTIVE)
    q = q.where(RoleAssignment.tenant_id == actor.tenant_id if actor.tenant_id else RoleAssignment.tenant_id.is_(None))
    rows = db.execute(q.order_by(RoleAssignment.record_seq, RoleAssignment.assignment_id)).scalars().all()
    return [a for a in rows if is_current(a, at) and _scope_fits(a, actor)]


def _scope_fits(a: RoleAssignment, actor: Actor) -> bool:
    if actor.tenant_id is None:
        return a.scope_type == PLATFORM
    if a.scope_type == TENANT:
        return a.scope_id == actor.tenant_id
    return a.scope_type == LEGAL_ENTITY and bool(a.scope_id)


def applies(a: RoleAssignment, entity_id: str | None) -> bool:
    """Whether `a` confers its permissions on a request whose resource resolved to `entity_id` (None: not resolved to
    one entity, e.g. a tenant-wide list). Entity-scoped roles need the exact entity (IAM-REQ-010, QA §09)."""
    return a.scope_type in (TENANT, PLATFORM) or (a.scope_type == LEGAL_ENTITY and entity_id is not None
                                                 and a.scope_id == entity_id)


def role_permissions(role_code: str) -> set[str]:
    from .security import ROLE_PERMISSIONS
    return set(ROLE_PERMISSIONS.get(role_code, set()))


def wide_roles(assignments: list[RoleAssignment]) -> list[str]:
    """Role codes held organisation- or platform-wide (what `org_actor.roles` used to mean)."""
    return sorted({a.role_code for a in assignments if a.scope_type in (TENANT, PLATFORM)})


def assignment_view(a: RoleAssignment) -> dict:
    from .platform import to_dict
    d = to_dict(a)
    d["permissions"] = sorted(role_permissions(a.role_code))
    d["in_force"] = is_current(a)
    return d


def effective_access(db: Session, actor: Actor) -> dict:
    """ZA-IAM-001 §23 ``GET /principals/{id}/effective-access``: roles, scopes and the permissions they confer now.
    No credential material (IAM-REQ-043)."""
    current = active_assignments(db, actor)
    by_entity: dict[str, set[str]] = {}
    for a in current:
        if a.scope_type == LEGAL_ENTITY:
            by_entity.setdefault(a.scope_id, set()).update(role_permissions(a.role_code))
    wide = set().union(*(role_permissions(a.role_code) for a in current if a.scope_type in (TENANT, PLATFORM)))
    return {"principal_id": actor.actor_id, "display_name": actor.display_name, "actor_type": actor.actor_type,
            "tenant_id": actor.tenant_id, "policy_version": AUTHZ_POLICY_VERSION, "evaluated_at": _now(),
            "assignments": [assignment_view(a) for a in current],
            "tenant_permissions": sorted(wide),
            "entity_permissions": {e: sorted(p | wide) for e, p in sorted(by_entity.items())}}


# ---------------------------------------------------------------- writes
def _record(db: Session, a: RoleAssignment, event_type: str, *, actor_id: str, reason: str | None,
            authority: list[str], record_seq: int) -> RoleAssignmentEvent:
    ev = RoleAssignmentEvent(tenant_id=a.tenant_id, assignment_id=a.assignment_id, principal_id=a.principal_id,
                             event_type=event_type, role_code=a.role_code, scope_type=a.scope_type, scope_id=a.scope_id,
                             valid_from=a.valid_from, valid_to=a.valid_to, source=a.source, actor_id=actor_id,
                             reason=reason, authorized_by=authority, policy_version=AUTHZ_POLICY_VERSION,
                             record_seq=record_seq)
    db.add(ev)
    return ev


def _sync_role_cache(db: Session, principal: Actor) -> None:
    """Keep org_actor.roles equal to the principal's current tenant/platform-wide roles (the pre-0009 meaning)."""
    db.flush()
    principal.roles = wide_roles(active_assignments(db, principal))


def provision_roles(db: Session, actor: Actor, roles: list[str], *, source: str, granted_by: Actor | None = None,
                    authority: list[str] | None = None, reason: str) -> list[RoleAssignment]:
    """Organisation- or platform-wide assignments for a principal created by a governed operator path: the one-time
    account bootstrap, a Super Admin appointing an Organisation Admin, or the demo seed. Not reachable from the
    tenant role API. Each assignment is recorded (event + ``role.assigned`` audit) like any other."""
    out = []
    db.flush()
    grantor = granted_by.actor_id if granted_by is not None else None
    for role in roles:
        seq = next_record_seq(db)
        a = RoleAssignment(tenant_id=actor.tenant_id, principal_id=actor.actor_id, role_code=role,
                           scope_type=TENANT if actor.tenant_id else PLATFORM, scope_id=actor.tenant_id,
                           valid_from=_now(), status=ACTIVE, source=source, granted_by=grantor, reason=reason,
                           record_seq=seq)
        db.add(a)
        db.flush()
        _record(db, a, ASSIGNED_EVENT, actor_id=grantor or f"system:{source.lower()}"[:36], reason=reason,
                authority=list(authority or []), record_seq=seq)
        audit(db, action="role.assigned", object_type="RoleAssignment", object_id=a.assignment_id, actor=granted_by,
              tenant_id=actor.tenant_id, new=f"{role}@{a.scope_type}", record_seq=seq,
              reason=f"{source}: {role} for {actor.actor_id} at {a.scope_type} scope; {reason}")
        out.append(a)
    _sync_role_cache(db, actor)
    return out


def _grantor_holds(ctx, rule: Assignability) -> list[str]:
    """The grantor's organisation-wide assignments that satisfy `rule` (empty: not allowed)."""
    return [a.assignment_id for a in ctx.grants()
            if a.scope_type == TENANT and rule.by_permission in role_permissions(a.role_code)]


def _target(ctx, principal_id: str) -> Actor:
    """The principal, if it belongs to the caller's organisation; otherwise 404 whether or not it exists elsewhere."""
    p = ctx.db.get(Actor, principal_id, with_for_update=True)  # one change per principal at a time
    if p is None or p.tenant_id != ctx.tenant:
        raise HTTPException(404, "principal not found")
    return p


def assign_role(ctx, *, principal_id: str, role_code: str, scope_type: str, scope_id: str | None,
                valid_from: datetime | None, valid_to: datetime | None, reason: str) -> RoleAssignment:
    """Assign `role_code` to a principal of the caller's organisation at an explicit scope (ZA-IAM-001 §23
    ``POST /role-assignments``: authorisation + SoD). Refusals are recorded as ``authorization.denied``."""
    from .security import ROLE_PERMISSIONS
    if role_code not in ROLE_PERMISSIONS:
        raise HTTPException(422, f"Unknown role {role_code!r}")
    rule = ASSIGNABILITY.get(role_code)
    if rule is None or not rule.assignable:
        raise ctx.deny("ROLE_NOT_ASSIGNABLE", f"Role {role_code} cannot be granted through the organisation's role "
                                              "registry (ZA-IAM-001 §08, §14, §15).")
    authority = _grantor_holds(ctx, rule)
    if not authority:
        raise ctx.deny("NOT_DELEGABLE_BY_ACTOR", f"Assigning {role_code} needs permission '{rule.by_permission}' "
                                                 "held organisation-wide.")
    if principal_id == ctx.actor.actor_id:  # IAM-REQ-021, IAM §10
        raise ctx.deny("SELF_ASSIGNMENT", "A principal cannot assign a role to itself (ZA-IAM-001 IAM-REQ-021).")
    principal = _target(ctx, principal_id)
    if principal.actor_type not in rule.principal_types:  # IAM-REQ-002: principal classes never inherit each other
        raise HTTPException(422, f"Role {role_code} can be held only by {sorted(rule.principal_types)} principals; "
                                 f"this principal is {principal.actor_type}.")
    if scope_type not in rule.scope_types:
        raise HTTPException(422, f"Role {role_code} can be assigned only at scope {sorted(rule.scope_types)}.")
    if scope_type == TENANT:
        if scope_id not in (None, ctx.tenant):
            raise HTTPException(422, "A TENANT-scope assignment is for the caller's own organisation; omit scope_id.")
        scope_id = ctx.tenant
    else:  # LEGAL_ENTITY: an entity of this organisation, resolved server-side (IAM-REQ-009)
        ent = ctx.db.get(LegalEntity, scope_id) if scope_id else None
        if ent is None or ent.tenant_id != ctx.tenant:
            raise HTTPException(404, "entity not found")
    now = _now()
    start = ensure_utc(valid_from) or now
    if start < now - CLOCK_SKEW:
        raise HTTPException(422, "valid_from cannot be in the past: an assignment cannot claim earlier authority.")
    end = ensure_utc(valid_to)
    if end is not None and end <= start:
        raise HTTPException(422, "valid_to must be later than valid_from.")
    for a in ctx.db.execute(select(RoleAssignment).where(
            RoleAssignment.tenant_id == ctx.tenant, RoleAssignment.principal_id == principal.actor_id,
            RoleAssignment.role_code == role_code, RoleAssignment.scope_type == scope_type,
            RoleAssignment.scope_id == scope_id, RoleAssignment.status == ACTIVE)).scalars():
        a_end = ensure_utc(a.valid_to)
        if (a_end is None or a_end > start) and (end is None or ensure_utc(a.valid_from) < end):
            raise HTTPException(409, f"{role_code} is already assigned to this principal at this scope for an "
                                     f"overlapping period (assignment {a.assignment_id}).")
    seq = next_record_seq(ctx.db)
    a = RoleAssignment(tenant_id=ctx.tenant, principal_id=principal.actor_id, role_code=role_code, scope_type=scope_type,
                       scope_id=scope_id, valid_from=start, valid_to=end, status=ACTIVE, source="ADMIN",
                       granted_by=ctx.actor.actor_id, reason=reason.strip(), record_seq=seq)
    ctx.db.add(a)
    ctx.db.flush()
    _record(ctx.db, a, ASSIGNED_EVENT, actor_id=ctx.actor.actor_id, reason=a.reason, authority=authority, record_seq=seq)
    audit(ctx.db, action="role.assigned", object_type="RoleAssignment", object_id=a.assignment_id, actor=ctx.actor,
          tenant_id=ctx.tenant, new=f"{role_code}@{scope_type}", record_seq=seq,
          reason=(f"{role_code} for {principal.actor_id} at {scope_type} {scope_id}; valid {start.isoformat()} to "
                  f"{end.isoformat() if end else 'open'}; grantor authority {','.join(authority)}; "
                  f"policy {AUTHZ_POLICY_VERSION}; {a.reason}")[:2000])
    _sync_role_cache(ctx.db, principal)
    return a


def revoke_role(ctx, assignment_id: str, reason: str) -> RoleAssignment:
    """Close an assignment now (IAM §22: revocation is a closure; the row and its history remain)."""
    a = ctx.db.get(RoleAssignment, assignment_id, with_for_update=True)  # waits for in-flight commits it authorised
    if a is None or a.tenant_id != ctx.tenant:
        raise HTTPException(404, "role assignment not found")
    rule = ASSIGNABILITY.get(a.role_code)
    if rule is None:
        raise ctx.deny("ROLE_NOT_REVOCABLE", f"Role {a.role_code} is not managed through the organisation's role "
                                             "registry.")
    authority = _grantor_holds(ctx, rule)
    if not authority:
        raise ctx.deny("NOT_DELEGABLE_BY_ACTOR", f"Revoking {a.role_code} needs permission '{rule.by_permission}' "
                                                 "held organisation-wide.")
    if a.status != ACTIVE:
        raise HTTPException(409, f"Role assignment is already {a.status}.")
    principal = _target(ctx, a.principal_id)
    now, seq = _now(), next_record_seq(ctx.db)
    prior_end = ensure_utc(a.valid_to)
    a.status, a.revoked_at, a.revoked_by, a.revoke_reason, a.revoked_record_seq = (
        REVOKED, now, ctx.actor.actor_id, reason.strip(), seq)
    if prior_end is None or prior_end > now:  # closure: never later than the revocation, never before the start
        a.valid_to = max(now, ensure_utc(a.valid_from))
    ctx.db.flush()
    _record(ctx.db, a, REVOKED_EVENT, actor_id=ctx.actor.actor_id, reason=a.revoke_reason, authority=authority,
            record_seq=seq)
    audit(ctx.db, action="role.revoked", object_type="RoleAssignment", object_id=a.assignment_id, actor=ctx.actor,
          tenant_id=ctx.tenant, prior=ACTIVE, new=REVOKED, record_seq=seq,
          reason=(f"{a.role_code} for {a.principal_id} at {a.scope_type} {a.scope_id}; grantor authority "
                  f"{','.join(authority)}; policy {AUTHZ_POLICY_VERSION}; {a.revoke_reason}")[:2000])
    _sync_role_cache(ctx.db, principal)
    return a


def assignments_of_tenant(db: Session, tenant_id: str, principal_id: str | None = None,
                          include_closed: bool = False) -> list[RoleAssignment]:
    q = select(RoleAssignment).where(RoleAssignment.tenant_id == tenant_id)
    if principal_id:
        q = q.where(RoleAssignment.principal_id == principal_id)
    if not include_closed:
        q = q.where(RoleAssignment.status == ACTIVE, or_(RoleAssignment.valid_to.is_(None),
                                                         RoleAssignment.valid_to > _now()))
    return db.execute(q.order_by(RoleAssignment.principal_id, RoleAssignment.record_seq)).scalars().all()
