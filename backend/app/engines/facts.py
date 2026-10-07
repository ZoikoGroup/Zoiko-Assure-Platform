"""Bitemporal fact store + deterministic fact resolution (ZA-TIM-001 §§4-6, ZA-APP-001 §4).

Writes never update historical content: the current record slice is closed at a
new record watermark and replacement segments are inserted (interval carving).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import FactAssertion, FactConflict, FactDefinition, Subject, new_id
from ..platform import emit, ensure_utc, next_record_seq

FAR_FUTURE = None  # open upper bound represented as NULL (no sentinel dates, TIM-REQ-002)


def _lt(a: datetime | None, b: datetime | None) -> bool:
    """a < b where None means +infinity."""
    if a is None:
        return False
    if b is None:
        return True
    return ensure_utc(a) < ensure_utc(b)


def overlaps(a_from, a_to, b_from, b_to) -> bool:
    return _lt(ensure_utc(a_from), b_to) and _lt(ensure_utc(b_from), a_to)


def typed_columns(fd: FactDefinition, value) -> dict:
    """Validate value against FactDefinition type; return typed column payload (no implicit coercion)."""
    t = fd.data_type
    cols = dict(value_boolean=None, value_integer=None, value_decimal=None, value_text=None,
                value_date=None, value_set=None)
    try:
        if t == "BOOLEAN":
            if not isinstance(value, bool):
                raise ValueError
            cols["value_boolean"] = value
        elif t == "INTEGER":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError
            cols["value_integer"] = value
        elif t == "DECIMAL":
            cols["value_decimal"] = str(Decimal(str(value)))
        elif t == "DATE":
            from datetime import date
            cols["value_date"] = date.fromisoformat(str(value))
        elif t.startswith("SET<"):
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError
            if fd.enum_domain and any(x not in fd.enum_domain for x in value):
                raise ValueError
            cols["value_set"] = sorted(set(value))
        else:  # ENUM, STRING, COUNTRY, JURISDICTION
            if not isinstance(value, str):
                raise ValueError
            if fd.enum_domain and value not in fd.enum_domain:
                raise ValueError
            cols["value_text"] = value
    except Exception:
        raise HTTPException(422, f"Value {value!r} is not a valid {t} for fact {fd.key}"
                                 + (f" (domain {fd.enum_domain})" if fd.enum_domain else ""))
    return cols


def assertion_value(a: FactAssertion):
    t = a.value_type
    if t == "BOOLEAN":
        return a.value_boolean
    if t == "INTEGER":
        return a.value_integer
    if t == "DECIMAL":
        return str(a.value_decimal) if a.value_decimal is not None else None
    if t == "DATE":
        return a.value_date.isoformat() if a.value_date else None
    if t.startswith("SET<"):
        return a.value_set
    return a.value_text


def assert_fact(db: Session, *, tenant_id: str, subject: Subject, fd: FactDefinition, value,
                valid_from: datetime, valid_to: datetime | None, provenance_class: str,
                source_ref: str | None, actor_id: str, change_kind: str = "CREATE",
                reason: str | None = None) -> list[FactAssertion]:
    """Record a fact revision with deterministic interval carving (TIM-REQ-007/008)."""
    if change_kind not in {"CREATE", "PROSPECTIVE_CHANGE", "CORRECTION", "RETROACTIVE_CORRECTION",
                           "WITHDRAWAL", "LATE_ARRIVAL"}:
        raise HTTPException(422, "Invalid change_kind")
    if change_kind != "CREATE" and not reason:
        raise HTTPException(422, "change_reason is mandatory for non-create changes")
    if provenance_class == "P5_INFERRED_AI" and change_kind != "CREATE":
        pass
    valid_from, valid_to = ensure_utc(valid_from), ensure_utc(valid_to)
    if valid_to is not None and valid_to <= valid_from:
        raise HTTPException(422, "Empty/negative valid range prohibited")
    cols = typed_columns(fd, value) if change_kind != "WITHDRAWAL" else None
    seq = next_record_seq(db)
    batch = new_id()
    now = datetime.now(timezone.utc)

    current = db.execute(select(FactAssertion).where(
        FactAssertion.tenant_id == tenant_id, FactAssertion.subject_id == subject.subject_id,
        FactAssertion.fact_definition_id == fd.fact_definition_id,
        FactAssertion.record_to_seq.is_(None))).scalars().all()
    lineage = current[0].lineage_id if current else new_id()
    created: list[FactAssertion] = []
    corrected_ids = []
    for cur in current:
        if not overlaps(cur.valid_from, cur.valid_to, valid_from, valid_to):
            continue
        cur.record_to_seq = seq  # close record slice — history preserved, never overwritten
        corrected_ids.append(cur.revision_id)
        # surviving slices before / after the new interval
        if _lt(cur.valid_from, valid_from):
            created.append(_clone(cur, cur.valid_from, valid_from, seq, batch, now, "SPLIT", actor_id))
        if valid_to is not None and _lt(valid_to, cur.valid_to):
            created.append(_clone(cur, valid_to, cur.valid_to, seq, batch, now, "SPLIT", actor_id))
    if cols is not None:
        created.append(FactAssertion(
            lineage_id=lineage, tenant_id=tenant_id, entity_id=subject.entity_id, subject_id=subject.subject_id,
            fact_definition_id=fd.fact_definition_id, value_type=fd.data_type, **cols,
            provenance_class=provenance_class, source_ref=source_ref,
            semantic_origin="CUSTOMER_ATTESTED" if provenance_class == "P3_CUSTOMER_ATTESTED" else "CUSTOMER_FACT",
            valid_from=valid_from, valid_to=valid_to, recorded_at=now, record_from_seq=seq,
            record_batch_id=batch, change_kind=change_kind, change_reason=reason,
            correction_of_revision_id=corrected_ids[0] if corrected_ids and "CORRECTION" in change_kind else None,
            actor_id=actor_id))
    for c in created:
        db.add(c)
    db.flush()
    emit(db, event_type="fact.changed", aggregate_type="FactAssertion", aggregate_id=lineage, tenant_id=tenant_id,
         entity_id=subject.entity_id, record_seq=seq,
         payload={"fact_definition": fd.key, "subject_id": subject.subject_id, "change_kind": change_kind,
                  "closed_revisions": corrected_ids, "new_revisions": [c.revision_id for c in created],
                  "valid_range": [valid_from, valid_to]})
    if change_kind in ("RETROACTIVE_CORRECTION", "CORRECTION", "LATE_ARRIVAL"):
        emit(db, event_type="temporal.retroactive_change_detected", aggregate_type="FactAssertion",
             aggregate_id=lineage, tenant_id=tenant_id, record_seq=seq,
             payload={"affected_valid_period": [valid_from, valid_to], "object_classes": ["ApplicabilityEvaluation"]})
    return created


def _clone(cur: FactAssertion, vf, vt, seq, batch, now, kind, actor_id) -> FactAssertion:
    return FactAssertion(
        lineage_id=cur.lineage_id, tenant_id=cur.tenant_id, entity_id=cur.entity_id, subject_id=cur.subject_id,
        fact_definition_id=cur.fact_definition_id, value_type=cur.value_type, value_boolean=cur.value_boolean,
        value_integer=cur.value_integer, value_decimal=cur.value_decimal, value_text=cur.value_text,
        value_date=cur.value_date, value_set=cur.value_set, provenance_class=cur.provenance_class,
        source_ref=cur.source_ref, semantic_origin=cur.semantic_origin, valid_from=vf, valid_to=vt,
        recorded_at=now, record_from_seq=seq, record_batch_id=batch, change_kind=kind,
        change_reason=f"Surviving slice of {cur.revision_id}", actor_id=actor_id)


def visible_at(stmt, model, known_seq: int):
    """Record-axis predicate: record_from <= K < record_to (or open)."""
    return stmt.where(model.record_from_seq <= known_seq,
                      or_(model.record_to_seq.is_(None), model.record_to_seq > known_seq))


def fact_subject_for(db: Session, subject: Subject, fd: FactDefinition) -> Subject | None:
    """Resolve which governed subject holds the fact (e.g. entity facts for an AI asset)."""
    if fd.subject_type == subject.subject_type:
        return subject
    if fd.subject_type == "LEGAL_ENTITY" and subject.entity_id:
        return db.execute(select(Subject).where(Subject.entity_id == subject.entity_id,
                                                Subject.subject_type == "LEGAL_ENTITY")).scalars().first()
    return None


def resolve_fact(db: Session, *, tenant_id: str, subject: Subject, fd: FactDefinition,
                 effective_at: datetime, known_seq: int, create_conflicts: bool = True) -> dict:
    """Deterministic fact resolution algorithm (ZA-APP-001 §4, steps 1-7)."""
    effective_at = ensure_utc(effective_at)
    holder = fact_subject_for(db, subject, fd)
    if holder is None:
        return {"known": False, "reason": "UNSUPPORTED_SOURCE", "rejected": []}
    stmt = select(FactAssertion).where(
        FactAssertion.tenant_id == tenant_id, FactAssertion.subject_id == holder.subject_id,
        FactAssertion.fact_definition_id == fd.fact_definition_id,
        FactAssertion.valid_from <= effective_at,
        or_(FactAssertion.valid_to.is_(None), FactAssertion.valid_to > effective_at))
    cands = db.execute(visible_at(stmt, FactAssertion, known_seq)).scalars().all()
    rejected = []
    admissible = []
    for c in cands:
        allowed = list(fd.allowed_provenance or [])
        if fd.manual_attestation_policy == "DISALLOWED" and "P3_CUSTOMER_ATTESTED" in allowed:
            allowed.remove("P3_CUSTOMER_ATTESTED")
        if c.provenance_class == "P5_INFERRED_AI" or c.provenance_class not in allowed:
            rejected.append({"revision_id": c.revision_id, "reason": "LOW_PROVENANCE", "provenance": c.provenance_class})
            continue
        if fd.expiry_days and ensure_utc(c.recorded_at) + timedelta(days=fd.expiry_days) < effective_at:
            rejected.append({"revision_id": c.revision_id, "reason": "FACT_EXPIRED"})
            continue
        admissible.append(c)
    if not admissible:
        reason = rejected[0]["reason"] if rejected else "MISSING_REQUIRED_FACT"
        return {"known": False, "reason": reason, "rejected": rejected}
    values = {str(assertion_value(a)) for a in admissible}
    if len(values) > 1:
        if create_conflicts:
            ids = sorted(a.revision_id for a in admissible)
            exists = db.execute(select(FactConflict).where(FactConflict.tenant_id == tenant_id,
                                                           FactConflict.subject_id == holder.subject_id,
                                                           FactConflict.fact_definition_id == fd.fact_definition_id,
                                                           FactConflict.status == "OPEN")).scalars().first()
            if not exists:
                db.add(FactConflict(tenant_id=tenant_id, subject_id=holder.subject_id,
                                    fact_definition_id=fd.fact_definition_id, candidate_revision_ids=ids))
        return {"known": False, "reason": "FACT_CONFLICT", "rejected": rejected,
                "candidates": [a.revision_id for a in admissible]}
    chosen = sorted(admissible, key=lambda a: a.record_from_seq)[-1]
    val = assertion_value(chosen)
    return {"known": True, "value": val, "revision_id": chosen.revision_id,
            "provenance_class": chosen.provenance_class, "holder_subject_id": holder.subject_id,
            "valid_from": chosen.valid_from, "valid_to": chosen.valid_to,
            "sensitivity": fd.sensitivity, "rejected": rejected}
