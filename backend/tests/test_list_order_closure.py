"""Remaining deterministic-ordering gaps closed after the list-endpoint audit (test_list_order_determinism covers the
earlier sweep).

  * GET /catalog/sources/{id} and GET /catalog/sources/{id}/trace: requirement versions ordered by requirement_key and
    record_seq, now ending in requirement_version_id;
  * GET /obligations/{lineage} (the dossier): revisions ordered by record_from_seq, now ending in revision_id; the
    obligation's exceptions and compensations, previously unordered, in the orders GET /exceptions and
    GET /compensations already use (valid_from descending, then ID), in the current and the as-of perspective.

Tied rows are inserted in an order that is neither sorted nor reversed, so insertion/physical order cannot pass.
Rows of another organisation that name the same obligation lineage stay out of the dossier.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TP, TQ = f"tenant-loc-{TAG}", f"tenant-loc-q-{TAG}"
CCO = f"loc-cco-{TAG}"
SUBJECT = f"subj-loc-{TAG}"
LINEAGE = f"lin-loc-{TAG}"
CONTENT = "actor-content-analyst"
NEW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
OLD = NEW - timedelta(days=30)
TIED = ("c", "a", "b")
S: dict = {}


def _id(kind: str, s: str) -> str:
    return f"loc-{TAG}-{kind}-{s}"


@pytest.fixture(scope="module")
def world(client):
    from app.db import CELL_ID, AdminSession
    from app.models import (Compensation, ExceptionRecord, Instrument, LegalEntity, ObligationInstance, RegulatorySource,
                            RequirementVersion, Subject, Tenant)
    from app.platform import current_watermark, sha256_hex
    with AdminSession() as db:
        seq = current_watermark(db)["record_seq"]  # real sequence values, so the as-of perspective can include them
        S["seq"] = seq
        for tid in (TP, TQ):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        db.add(LegalEntity(entity_id=f"entity-{TP}", tenant_id=TP, legal_name=f"{TP} AG", country="DE",
                           home_cell_id=CELL_ID, subject_id=SUBJECT))
        db.add(Subject(subject_id=SUBJECT, tenant_id=TP, entity_id=f"entity-{TP}", subject_type="LEGAL_ENTITY",
                       display_name=f"{TP} AG"))
        add_principal(db, actor_id=CCO, tenant_id=TP, actor_type="HUMAN", display_name=CCO, roles=["CCO"])
        inst = Instrument(identifier=f"LOC-{TAG}", title="Ordering closure probe", jurisdiction="EU", authority="Probe",
                          coverage_mode="ENCODED")
        db.add(inst)
        db.flush()
        text = f"Entities shall keep ordered records {TAG}."
        src = RegulatorySource(source_id=_id("src", "x"), authority="Probe", jurisdiction="EU", source_type="GUIDANCE",
                               title="Ordering closure probe", locator=f"loc-{TAG}", fingerprint=sha256_hex(text),
                               rights_class="OFFICIAL_REUSE_PERMITTED", raw_text=text, lifecycle="CLASSIFIED",
                               record_seq=seq)
        db.add(src)
        db.flush()

        def add(make, kind, tied_value, old_value=None):
            for s in TIED:
                db.add(make(_id(kind, s), tied_value))
                db.flush()  # one INSERT per row, in this order
            if old_value is not None:
                db.add(make(_id(kind, "old"), old_value))
                db.flush()
        # Requirement versions tied on (requirement_key, record_seq).
        add(lambda i, v: RequirementVersion(
            requirement_version_id=i, requirement_key=f"LOC-{TAG}", instrument_id=inst.instrument_id,
            source_id=src.source_id, title="Probe", locator="Art. 1", valid_from=date(2025, 1, 1), record_seq=v), "rv", seq)
        # Obligation revisions tied on record_from_seq: one current, the others closed; one older revision.
        add(lambda i, v: ObligationInstance(
            revision_id=i, lineage_id=LINEAGE, tenant_id=TP, subject_id=SUBJECT, obligation_definition_id="-",
            determination_revision_id="-", attestation_id="-", valid_from=OLD, record_from_seq=v,
            record_to_seq=None if i.endswith("-c") else v), "obl", seq, seq - 1)
        db.get(ObligationInstance, _id("obl", "old")).record_to_seq = seq - 1
        add(lambda i, v: ExceptionRecord(
            exception_id=i, tenant_id=TP, target_type="OBLIGATION", target_id=LINEAGE, reason="ordering probe",
            requested_by=CCO, valid_from=v, valid_to=v + timedelta(days=90), record_seq=seq), "exc", NEW, OLD)
        add(lambda i, v: Compensation(
            compensation_id=i, tenant_id=TP, obligation_lineage_id=LINEAGE, primary_control_key="C_PROBE",
            compensating_expression={}, equivalence_rationale="ordering probe", valid_from=v, record_seq=seq),
            "cmp", NEW, OLD)
        # Another organisation's rows naming the same lineage never appear in this dossier.
        db.add(ExceptionRecord(exception_id=_id("exc", "other"), tenant_id=TQ, target_type="OBLIGATION",
                               target_id=LINEAGE, reason="other organisation", requested_by="x", valid_from=NEW,
                               valid_to=NEW + timedelta(days=90), record_seq=seq))
        db.add(Compensation(compensation_id=_id("cmp", "other"), tenant_id=TQ, obligation_lineage_id=LINEAGE,
                            primary_control_key="C_PROBE", compensating_expression={},
                            equivalence_rationale="other organisation", valid_from=NEW, record_seq=seq))
        db.commit()
    return True


SORTED = sorted(TIED)


def test_source_detail_and_trace_break_requirement_version_ties_by_id(client, world):
    expected = [_id("rv", s) for s in SORTED]
    detail = client.get(f"/api/catalog/sources/{_id('src', 'x')}", headers=H(CONTENT))
    assert detail.status_code == 200, detail.text
    assert [r["requirement_version_id"] for r in detail.json()["requirement_versions"]] == expected
    trace = client.get(f"/api/catalog/sources/{_id('src', 'x')}/trace", headers=H(CONTENT))
    assert trace.status_code == 200, trace.text
    assert [r["requirement"]["requirement_version_id"] for r in trace.json()["requirements"]] == expected
    # The same for every reader and on every read.
    again = client.get(f"/api/catalog/sources/{_id('src', 'x')}", headers=H("actor-cco")).json()
    assert [r["requirement_version_id"] for r in again["requirement_versions"]] == expected


@pytest.mark.parametrize("as_of", [False, True])
def test_dossier_revisions_exceptions_and_compensations_are_deterministic(client, world, as_of):
    path = f"/api/obligations/{LINEAGE}" + (f"?as_of_record_seq={S['seq']}" if as_of else "")
    r = client.get(path, headers=H(CCO))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["revision_id"] for x in body["revisions"]] == [_id("obl", "old"), *(_id("obl", s) for s in SORTED)]
    assert [x["exception_id"] for x in body["exceptions"]] == [*(_id("exc", s) for s in SORTED), _id("exc", "old")]
    assert [x["compensation_id"] for x in body["compensations"]] == [*(_id("cmp", s) for s in SORTED),
                                                                     _id("cmp", "old")]
    assert client.get(path, headers=H(CCO)).json()["exceptions"] == body["exceptions"]  # stable across reads
    # The other organisation's rows naming the same lineage are not part of this dossier.
    assert _id("exc", "other") not in r.text and _id("cmp", "other") not in r.text
