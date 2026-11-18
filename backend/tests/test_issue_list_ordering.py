"""GET /api/issues returns a deterministic order: newest first (created_at descending, unchanged), then issue_id as
the unique tie-breaker for issues created at the same instant.

The endpoint has no filters and no pagination, so there are no page boundaries to cover; what is pinned is that the
same records always come back in the same order, also after an UPDATE, and that the list stays within the caller's
organisation. Issues are inserted directly for probe organisations, in an order that is neither creation order nor
issue_id order, so insertion/physical order cannot pass for the contract: the tie test fails without the issue_id
tie-breaker on SQLite and PostgreSQL. The update test is a stability check only; a state-only UPDATE is a HOT update on
PostgreSQL that keeps the row's index position, so it does not by itself reorder an unsorted result.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-ilo-a-{TAG}", f"tenant-ilo-b-{TAG}"
CCO_A, CCO_B = f"ilo-cco-a-{TAG}", f"ilo-cco-b-{TAG}"
T1 = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
T2, T3 = T1 + timedelta(hours=1), T1 + timedelta(hours=2)
# (issue_id suffix, created_at), in insertion order: ties at T1 and T2, inserted out of issue_id order.
ROWS = [("e", T1), ("d", T2), ("a", T1), ("f", T3), ("b", T2), ("c", T1)]


def _iid(tenant_key: str, suffix: str) -> str:
    return f"iss-ilo-{tenant_key}-{TAG}-{suffix}"


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Actor, Issue, Tenant
    with AdminSession() as db:
        for tid, cco, key in ((TA, CCO_A, "a"), (TB, CCO_B, "b")):
            if db.get(Tenant, tid) is None:
                db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
                db.flush()
            if db.get(Actor, cco) is None:
                add_principal(db, actor_id=cco, tenant_id=tid, actor_type="HUMAN", display_name=cco, roles=["CCO"])
            for suffix, at in ROWS:
                iid = _iid(key, suffix)
                db.add(Issue(issue_id=iid, tenant_id=tid, title=f"Ordering probe {suffix}", issue_type="CONTROL_REVIEW",
                             source_ref_type="ControlEvaluation", source_ref_id=f"ce-ilo-{TAG}-{suffix}",
                             dedupe_key=f"ilo:{TAG}:{key}:{suffix}", created_at=at))
                db.flush()  # one INSERT per row, in this order
        db.commit()
    return True


def _listed(client, actor):
    r = client.get("/api/issues", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()


def test_ties_on_creation_time_are_ordered_by_issue_id_after_newest_first(client, world):
    body = _listed(client, CCO_A)
    expected = [_iid("a", s) for s in ("f", "b", "d", "a", "c", "e")]  # T3; T2: b, d; T1: a, c, e
    assert [i["issue_id"] for i in body] == expected
    stamps = [datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")) for i in body]
    stamps = [s if s.tzinfo else s.replace(tzinfo=timezone.utc) for s in stamps]
    assert stamps == sorted(stamps, reverse=True)  # the existing primary order is unchanged: newest first


def test_the_order_survives_repeated_reads_and_an_update_to_a_tied_issue(client, world):
    first = [i["issue_id"] for i in _listed(client, CCO_A)]
    assert all([i["issue_id"] for i in _listed(client, CCO_A)] == first for _ in range(3))
    # A transition UPDATEs the row; the listed order must not change because of it.
    moved = _iid("a", "a")
    r = client.post(f"/api/issues/{moved}/transition", headers=H(CCO_A),
                    json={"state": "CANCELLED", "reason": "ordering probe: duplicate"})
    assert r.status_code == 200, r.text
    after = _listed(client, CCO_A)
    assert [i["issue_id"] for i in after] == first
    assert next(i for i in after if i["issue_id"] == moved)["state"] == "CANCELLED"


def test_each_organisation_lists_only_its_own_issues_in_the_same_order(client, world):
    a = [i["issue_id"] for i in _listed(client, CCO_A)]
    b = [i["issue_id"] for i in _listed(client, CCO_B)]
    assert b == [_iid("b", s) for s in ("f", "b", "d", "a", "c", "e")]
    assert not set(a) & set(b)
    assert client.get("/api/issues").status_code == 401
