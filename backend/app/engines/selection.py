"""Selections that must resolve to exactly one record, and refuse when more than one qualifies.

ZA-DAT-001 DAT-REQ-010: "Object classes requiring one current authoritative value per scope MUST enforce non-overlap
through constraints and serialized mutation logic." The schema (migrations 0001-0013) has no such constraint for the
records selected here: no UNIQUE (obligation_definition_id, version) on catalog_rule_version, and no partial unique
index or exclusion constraint on the current revisions of applicability_determination_revision or
assurance_obligation_instance_revision. Uniqueness rests on the serialized mutation logic alone (the definition row lock
at publication; the decision-scope lock at evaluation and attestation). Adding the constraints is a schema change for the
Data Architect (DAT-REQ-010, DAT-REQ-039), so none is created here.

What the application can do without a schema change is never resolve an impossible state silently. A helper that must
return THE current or effective record takes every row that qualifies and refuses when there is more than one: it neither
picks by physical row order, nor by an ordering rule that no specification defines for that case (a newest-first sort,
or an ID tie-break). The selection rules that are defined stay exactly as they were:

  * rule versions: among the PUBLISHED versions in force, the highest version number is the one bound (the existing
    supersession policy, unchanged). Only two rows sharing that version number are ambiguous;
  * current revisions: record_to_seq IS NULL (ZA-DAT-001 §14 "Current revision"); as of a record sequence K: the
    revision whose record slice [record_from_seq, record_to_seq) contains K (ZA-TIM-001 half-open semantics).

The refusal is an unexpected server fault, never a business state (ZA-API-001 §12 "500 Unexpected server fault";
"Infrastructure failure is never converted into ... another domain business state"). It names the record type, the
selection and how many records qualified; the candidate IDs are logged, not returned, so a caller who may not read the
records (an entity-scoped role resolving an obligation's entity) learns nothing about them. The error code
AMBIGUOUS_CURRENT_RECORD is code-local: ZA-API-001 §12 and ZA-APP-001 §12 define none for this case.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import HTTPException

log = logging.getLogger(__name__)

CODE = "AMBIGUOUS_CURRENT_RECORD"


class AmbiguousSelection(HTTPException):
    """More than one record qualifies where exactly one may (DAT-REQ-010). Nothing is selected and nothing is written:
    raised before any write the caller would make from the selection, so the request's transaction rolls back."""

    def __init__(self, record_type: str, selection: str, candidate_ids: Iterable[str]):
        ids = sorted(candidate_ids)
        super().__init__(500, {"error": CODE, "record_type": record_type, "selection": selection,
                               "candidates": len(ids),
                               "message": f"{len(ids)} {record_type} records qualify as the {selection} record where "
                                          "exactly one may; none is selected (DAT-REQ-010)"})
        self.record_type, self.selection, self.candidate_ids = record_type, selection, ids
        log.error("ambiguous %s selection of %s: candidates %s", selection, record_type, ids)


def one_or_none(rows: Iterable, *, record_type: str, selection: str, id_attr: str):
    """The only row, None for none, AmbiguousSelection for more than one."""
    rows = list(rows)
    if len(rows) > 1:
        raise AmbiguousSelection(record_type, selection, (getattr(r, id_attr) for r in rows))
    return rows[0] if rows else None


def highest_version(rows: Iterable, *, selection: str):
    """The RuleVersion with the highest version number among ``rows`` (the existing supersession policy), None for no
    rows, AmbiguousSelection when two or more rows share that highest number. Rows below it never make the selection
    ambiguous: they are not selected."""
    rows = list(rows)
    if not rows:
        return None
    top = max(r.version for r in rows)
    return one_or_none([r for r in rows if r.version == top], record_type="RuleVersion", selection=selection,
                       id_attr="rule_version_id")
