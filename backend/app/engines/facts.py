"""Bitemporal fact store + deterministic fact resolution (ZA-TIM-001 §§4-6, ZA-APP-001 §4).

Writes never update historical content: the current record slice is closed at a
new record watermark and replacement segments are inserted (interval carving).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import FactAssertion, FactConflict, FactDefinition, Subject, new_id
from ..platform import emit, ensure_utc, next_record_seq, to_dict
from . import rules

FAR_FUTURE = None  # open upper bound represented as NULL (no sentinel dates, TIM-REQ-002)
# applicability_fact_assertion_revision.value_text (varchar): the longest TIMESTAMP value a fact write can store as given.
_VALUE_TEXT_MAX = FactAssertion.__table__.c.value_text.type.length

# ---------------------------------------------------------------- value redaction
# Fact values are redacted by FactDefinition sensitivity (ZA-APP-001 APP-REQ-018 and §13; ZA-API-001 §25 "redact by field
# classification and viewer role while preserving stable reference/reason structure"; ZA-TIM-001 §22: the same in
# historical views). These are the classes and the marker the applicability router and the evaluation trace already
# used, kept in one place. The specifications name no viewer role that may see such a value, so none is exempt.
REDACTED_SENSITIVITIES = frozenset({"RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"})
REDACTED = "••• (redacted)"
# Every column of a FactAssertion revision that holds the value itself, read from the table so that a value column added
# later is withheld without another change here (value_type names the type, not the value).
VALUE_COLUMNS = tuple(c.key for c in FactAssertion.__table__.columns if c.key.startswith("value_") and c.key != "value_type")


def value_redacted(sensitivity: str | None) -> bool:
    return sensitivity in REDACTED_SENSITIVITIES


def display_value(sensitivity: str | None, value):
    """A resolved fact value as a response may show it."""
    return REDACTED if value_redacted(sensitivity) else value


def assertion_view(a: FactAssertion, fd: FactDefinition) -> dict:
    """A FactAssertion revision as API responses show it: its columns plus `value`. For a redacted sensitivity `value` is
    the marker and every typed value column is withheld (None): the same fields, the revision, lineage, provenance and
    time references unchanged, without the value (ZA-APP-001 §13 "can retain assertion IDs without value"). A response
    that shows a revision builds it here, never from the row's columns directly (ZA-API-001 §25: not "return full object
    then hide in UI")."""
    d = to_dict(a)
    if value_redacted(fd.sensitivity):
        for col in VALUE_COLUMNS:
            d[col] = None
        d["value"] = REDACTED
    else:
        d["value"] = assertion_value(a)
    return d


def input_bundle_view(bundle, sensitivities: dict[str, str | None]):
    """An evaluation's frozen EvaluationInputBundle as API responses show it (the same policy as assertion_view).

    `fact_inputs[].typed_value_hash` is an unkeyed digest of the consumed value (`digest({"v": value})`). For a
    low-entropy type (BOOLEAN, ENUM, COUNTRY, a small INTEGER range, ...) hashing each candidate value finds it, so for
    a redacted sensitivity the hash discloses the value and is withheld (None), as assertion_view withholds the typed
    value columns. The entry keeps its fact definition, assertion revision ID and provenance class (ZA-APP-001 §13
    "machine trace can retain assertion IDs without value"; APP-REQ-018 "without removing the underlying assertion
    reference"; ZA-API-001 §25). Every other field, and every entry of a non-redacted fact, is returned as stored.

    `sensitivities` maps FactDefinition key -> sensitivity. An entry whose definition is not in it is withheld too: its
    sensitivity cannot show that release is permitted. The stored bundle is never modified (the view is a new structure),
    so the record, its input_bundle_hash and evaluation_key, and replay are unchanged."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("fact_inputs"), list):
        return bundle

    def entry(fi):
        if not isinstance(fi, dict):
            return fi
        key = fi.get("fact_definition")
        if "typed_value_hash" in fi and (key not in sensitivities or value_redacted(sensitivities[key])):
            return {**fi, "typed_value_hash": None}
        return dict(fi)
    return {**bundle, "fact_inputs": [entry(fi) for fi in bundle["fact_inputs"]]}


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
            # value_integer is a signed 64-bit column (BIGINT): a value outside it is refused here, before anything is
            # written, instead of failing at persistence differently on PostgreSQL and SQLite (API-REQ-033).
            if isinstance(value, bool) or not isinstance(value, int) or not -(2 ** 63) <= value < 2 ** 63:
                raise ValueError
            cols["value_integer"] = value
        elif t == "DECIMAL":
            # RUL §05 "Exact decimal arithmetic": NaN, sNaN and +/-Infinity are refused (Decimal accepts them, and an
            # ordering comparison with NaN raises in the evaluator), and so is a value value_decimal (numeric(38,10))
            # cannot hold exactly: PostgreSQL rounded a longer fraction silently and failed on a longer integer part
            # only at persistence, after the record sequence was taken (API-REQ-033, as for INTEGER above).
            d = Decimal(str(value))
            if isinstance(value, bool) or not rules.decimal_value_ok(d):
                raise ValueError
            cols["value_decimal"] = str(d)
        elif t == "DATE":
            from datetime import date
            # A DATE is an ISO date string, as golden-vector inputs require (rules._fact_value_ok). str(value) also turned a
            # JSON number such as 20270101 into a date: an implicit coercion this function's contract excludes.
            if not isinstance(value, str):
                raise ValueError
            cols["value_date"] = date.fromisoformat(value)
        elif t == "TIMESTAMP":
            # ZA-RUL-001 §03 "Offset-aware canonical instant". TIMESTAMP used to fall through to the text branch below and
            # accept any string, which the evaluator then could not compare (malformed text, or a local time without an
            # offset against an instant). Storage is unchanged: the value as given, in value_text. That column holds
            # _VALUE_TEXT_MAX characters, so a longer instant (hundreds of fractional digits, all beyond the microsecond
            # the evaluator reads) is refused here, before the record sequence is taken, instead of failing at persistence
            # on PostgreSQL with a 500 (SQLite stored it), as an INTEGER or DECIMAL its column cannot hold is (API-REQ-033).
            if not isinstance(value, str) or rules.timestamp_value(value) is None or len(value) > _VALUE_TEXT_MAX:
                raise ValueError
            cols["value_text"] = value
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


def overlapping_current_revisions(db: Session, *, tenant_id: str, subject: Subject, fd: FactDefinition,
                                  valid_from: datetime, valid_to: datetime | None, known_seq: int) -> list[FactAssertion]:
    """The revisions of `fd` held by `subject` that were current knowledge at record sequence `known_seq` (visible_at:
    record_from <= K < record_to, the ZA-TIM-001 canonical bitemporal lookup) and whose valid interval overlaps
    [valid_from, valid_to): the revisions a fact write over that interval would close and carve as known at K
    (assert_fact, the same overlap predicate; the "old revision refs" of fact.changed, ZA-TIM-001 §17). Read-only.
    Ordered by valid time, then revision ID."""
    valid_from, valid_to = ensure_utc(valid_from), ensure_utc(valid_to)
    rows = db.execute(visible_at(select(FactAssertion).where(
        FactAssertion.tenant_id == tenant_id, FactAssertion.subject_id == subject.subject_id,
        FactAssertion.fact_definition_id == fd.fact_definition_id), FactAssertion, known_seq)).scalars().all()
    hit = [a for a in rows if overlaps(a.valid_from, a.valid_to, valid_from, valid_to)]
    return sorted(hit, key=lambda a: (ensure_utc(a.valid_from), a.revision_id))


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
        # The subject's own organisation only (DOM-INV-11): the query is scoped itself, not only by row-level security
        # (which SQLite does not have), and ordered, so the holder is the same on every read (APP §04 determinism).
        return db.execute(select(Subject).where(Subject.tenant_id == subject.tenant_id,
                                                Subject.entity_id == subject.entity_id,
                                                Subject.subject_type == "LEGAL_ENTITY")
                          .order_by(Subject.subject_id)).scalars().first()
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
