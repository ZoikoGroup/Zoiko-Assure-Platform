"""Global connector certification registry (FR-CON-001/002, INV-11, GLS-REQ-017).

ConnectorDefinition rows are shared by every organisation: customer-visible connector status is read only from this
registry, so a change here changes what every tenant sees. Only an explicitly authorised Zoiko platform principal
(tenant_id NULL, HUMAN, permission `certify_connectors`) may change it; no organisation role can, including an
organisation's PLATFORM_ADMIN, whose `manage_connectors` covers its own installations only. The check lives here, not
only on the route, so internal callers cannot bypass it. Every change is audited with actor, prior/new state and reason.

Since migration 0012 every effective change also appends a ConnectorCertificationEvent, and the database itself refuses
registry writes outside a verified platform-principal context. A request that changes nothing records nothing.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from .. import codes
from ..models import (ConnectorCertificationEvent, ConnectorDefinition, ExtractionContractVersion,
                      MappingContractVersion)
from ..platform import audit, emit, next_record_seq
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
    # Stage 3c (migration 0012): lock the row and re-read it, so concurrent changes serialise and each is recorded
    # against the state it actually replaced (docs/connector-certification-history-design.md §6).
    c = ctx.db.execute(select(ConnectorDefinition).where(ConnectorDefinition.connector_id == connector_id)
                       .with_for_update().execution_options(populate_existing=True)).scalars().first()
    if c is None:
        raise HTTPException(404, "connector not found")
    if certification == "CERTIFIED":
        failing = sorted(k for k in CERTIFICATION_MATRIX if c.certification_matrix.get(k) != "PASS")
        if failing:
            raise HTTPException(409, {"error": "CERTIFICATION_MATRIX_INCOMPLETE", "failing": failing})
    old_cert, old_health = c.certification, c.runtime_health
    new_health = runtime_health if runtime_health is not None else old_health
    if (certification, new_health) == (old_cert, old_health):
        return c  # nothing changes: a retry or duplicate records nothing (no history, audit or outbox row)
    seq = next_record_seq(ctx.db)
    prior_event = _prior_event(ctx.db, connector_id, "CONNECTOR_DEFINITION", None)
    c.certification, c.runtime_health = certification, new_health
    ev = ConnectorCertificationEvent(
        connector_id=connector_id, build_version=c.build_version, from_certification=old_cert,
        to_certification=certification, from_runtime_health=old_health, to_runtime_health=new_health,
        actor_id=ctx.actor.actor_id, reason=reason.strip(), prior_event_id=prior_event, record_seq=seq)
    ctx.db.add(ev)
    ctx.db.flush()
    # Platform record (tenant_id NULL): the registry belongs to no organisation. Audit contract unchanged.
    audit(ctx.db, action="connector.certification_changed", object_type="ConnectorDefinition", object_id=connector_id,
          actor=ctx.actor, tenant_id=None, prior=f"{old_cert}|{old_health}", new=f"{certification}|{new_health}",
          reason=reason.strip(), record_seq=seq)
    if certification != old_cert:
        # ZA-EVT-001 connector.certification_changed, STATE_CHANGED profile. A runtime-health-only change has no
        # catalogued event, so none is emitted (EVT-REQ-008); history and audit still record it. No free-text reason
        # in the payload (EVT-REQ-025); no controlled certification reason codes exist yet (design D6).
        emit(ctx.db, event_type="connector.certification_changed", aggregate_type="ConnectorDefinition",
             aggregate_id=connector_id, actor=ctx.actor_ref(), record_seq=seq, payload={
                 "resource_id": connector_id,
                 "prior_state": {"certification": old_cert, "runtime_health": old_health},
                 "new_state": {"certification": certification, "runtime_health": new_health},
                 "revision_ref": ev.event_id, "prior_revision_ref": prior_event,
                 "effective_scope": {"build_version": c.build_version}, "reason_codes": []})
    return c


def _prior_event(db, connector_id: str, subject_type: str, subject_id: str | None) -> str | None:
    """The latest history event of one certification subject: its revision chain (migration 0013)."""
    q = (select(ConnectorCertificationEvent.event_id)
         .where(ConnectorCertificationEvent.connector_id == connector_id,
                ConnectorCertificationEvent.subject_type == subject_type,
                ConnectorCertificationEvent.subject_id.is_(None) if subject_id is None
                else ConnectorCertificationEvent.subject_id == subject_id)
         .order_by(ConnectorCertificationEvent.record_seq.desc()).limit(1))
    return db.execute(q).scalar()


# Version-level certification (D2, migration 0013): IPE-REQ-028/029. A contract version is the certified subject for
# its exact extraction or mapping contract; its certification uses the ZA-GLS-001 codes. Open, and not decided here
# (docs/connector-version-certification-design.md): build identity and range (D2-e), envelope composition (D2-f), the
# registry relationship (D2-g), the effect on SourceRuns/IPE (D2-h; runs are unchanged) and per-version evidence (D2-i).
CONTRACT_KINDS = {
    "extraction": (ExtractionContractVersion, "EXTRACTION_CONTRACT_VERSION", "ExtractionContractVersion"),
    "mapping": (MappingContractVersion, "MAPPING_CONTRACT_VERSION", "MappingContractVersion"),
}


def set_contract_certification(ctx, connector_id: str, kind: str, version_id: str, *, certification: str,
                               reason: str):
    """Certify, suspend or revoke one exact contract version. Same authority as the registry (Zoiko platform HUMAN with
    certify_connectors), checked here so internal callers cannot bypass it; a reason is required; a request that
    changes nothing records nothing; the row is locked so concurrent changes serialise. CERTIFIED additionally needs the
    connector's certification matrix to pass (FR-CON-002): the strictest evidence available until per-version evidence
    is defined (D2-i)."""
    ctx.check(CERTIFY_CONNECTORS)
    if kind not in CONTRACT_KINDS:
        raise HTTPException(404, "unknown contract kind")
    if not reason or not reason.strip():
        raise HTTPException(422, "A reason is required for a registry transition")
    codes.assert_code("connector_certification", certification)
    model, subject_type, object_type = CONTRACT_KINDS[kind]
    pk = model.__table__.primary_key.columns.keys()[0]
    v = ctx.db.execute(select(model).where(getattr(model, pk) == version_id, model.connector_id == connector_id)
                       .with_for_update().execution_options(populate_existing=True)).scalars().first()
    if v is None:  # unknown, or a version of another connector: answered alike
        raise HTTPException(404, "contract version not found")
    c = ctx.db.get(ConnectorDefinition, connector_id)
    if certification == "CERTIFIED":
        failing = sorted(k for k in CERTIFICATION_MATRIX if (c.certification_matrix or {}).get(k) != "PASS")
        if failing:
            raise HTTPException(409, {"error": "CERTIFICATION_MATRIX_INCOMPLETE", "failing": failing})
    old = v.status
    if certification == old:
        return v  # nothing changes: a retry or duplicate records nothing
    seq = next_record_seq(ctx.db)
    prior_event = _prior_event(ctx.db, connector_id, subject_type, version_id)
    v.status = certification
    ev = ConnectorCertificationEvent(
        connector_id=connector_id, subject_type=subject_type, subject_id=version_id, build_version=c.build_version,
        from_certification=old, to_certification=certification, from_runtime_health=None, to_runtime_health=None,
        actor_id=ctx.actor.actor_id, reason=reason.strip(), prior_event_id=prior_event, record_seq=seq)
    ctx.db.add(ev)
    ctx.db.flush()
    audit(ctx.db, action="connector.certification_changed", object_type=object_type, object_id=version_id,
          actor=ctx.actor, tenant_id=None, prior=old, new=certification, reason=reason.strip(), record_seq=seq)
    source = ({"source_schema_version": v.source_schema_fingerprint} if kind == "mapping"
              else {"endpoint_id": v.endpoint_id})
    emit(ctx.db, event_type="connector.certification_changed", aggregate_type=object_type, aggregate_id=version_id,
         actor=ctx.actor_ref(), record_seq=seq, payload={
             "resource_id": version_id, "resource_type": subject_type,
             "prior_state": {"certification": old}, "new_state": {"certification": certification},
             "revision_ref": ev.event_id, "prior_revision_ref": prior_event,
             "effective_scope": {"connector_id": connector_id, "contract_version": v.version, **source},
             "reason_codes": []})
    return v
