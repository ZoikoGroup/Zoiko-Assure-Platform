"""Global connector certification registry (FR-CON-001/002, INV-11, GLS-REQ-017).

ConnectorDefinition rows are shared by every organisation: customer-visible connector status is read only from this
registry, so a change here changes what every tenant sees. Only an explicitly authorised Zoiko platform principal
(tenant_id NULL, HUMAN, permission `certify_connectors`) may change it; no organisation role can, including an
organisation's PLATFORM_ADMIN, whose `manage_connectors` covers its own installations only. The check lives here, not
only on the route, so internal callers cannot bypass it. Every change is audited with actor, prior/new state and reason.
"""
from __future__ import annotations

from fastapi import HTTPException

from .. import codes
from ..models import ConnectorDefinition
from ..platform import audit, emit
from ..security import Requirement

CERTIFY_CONNECTORS = Requirement(("certify_connectors",), human=True, platform=True)
# FR-CON-002: CERTIFIED only once auth scopes, field mapping, IPE behaviour, retries and tenancy isolation pass.
CERTIFICATION_MATRIX = {"auth_scope", "tenant_isolation", "pagination_snapshot", "population_proof", "schema_mapping",
                        "transforms", "retries_idempotency", "ipe_anomalies", "version_compatibility"}


def set_certification(ctx, connector_id: str, *, certification: str, runtime_health: str | None,
                      reason: str) -> ConnectorDefinition:
    ctx.check(CERTIFY_CONNECTORS)
    if not reason or not reason.strip():
        raise HTTPException(422, "A reason is required for a registry transition")
    codes.assert_code("connector_certification", certification)
    if runtime_health is not None:
        codes.assert_code("connector_runtime", runtime_health)
    c = ctx.db.get(ConnectorDefinition, connector_id)
    if c is None:
        raise HTTPException(404, "connector not found")
    if certification == "CERTIFIED":
        failing = sorted(k for k in CERTIFICATION_MATRIX if c.certification_matrix.get(k) != "PASS")
        if failing:
            raise HTTPException(409, {"error": "CERTIFICATION_MATRIX_INCOMPLETE", "failing": failing})
    prior = f"{c.certification}|{c.runtime_health}"
    c.certification = certification
    if runtime_health is not None:
        c.runtime_health = runtime_health
    new = f"{c.certification}|{c.runtime_health}"
    # Platform record (tenant_id NULL): the registry belongs to no organisation.
    audit(ctx.db, action="connector.certification_changed", object_type="ConnectorDefinition", object_id=connector_id,
          actor=ctx.actor, tenant_id=None, prior=prior, new=new, reason=reason.strip())
    emit(ctx.db, event_type="connector.certification_changed", aggregate_type="ConnectorDefinition",
         aggregate_id=connector_id, actor=ctx.actor_ref(),
         payload={"prior": prior, "new": new, "reason": reason.strip()})
    return c
