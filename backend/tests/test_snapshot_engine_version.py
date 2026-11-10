"""A snapshot reproduction never claims success under another roll-up engine version (STA-REQ-032; ZA-TIM-001 §10).

An AssuranceSnapshot pins the roll-up engine version it was computed with (rollup_engine_version). This runtime holds
exactly one engine (codes.ROLLUP_ENGINE_VERSION) and no policy exists for running an older one (version selection is
an open STA-REQ-032 / TIM-ADR-04 decision). So GET /snapshots/{id}/reproduce reports both versions and, when they
differ, answers ENGINE_CHANGED and reproducible=false instead of comparing another engine's result, as applicability
replay answers BUILD_CHANGED. Nothing is written by a reproduction. Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

from sqlalchemy import select

from conftest import H

TENANT = "tenant-zoiko-group"


def _seeded_snapshot():
    from app.db import AdminSession
    from app.models import AssuranceSnapshot
    from app.platform import current_watermark
    with AdminSession() as db:
        s = db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == TENANT)
                       .order_by(AssuranceSnapshot.created_at, AssuranceSnapshot.snapshot_id)).scalars().first()
        assert s is not None, "the seed persists assurance snapshots"
        return s.snapshot_id, s.rollup_engine_version, s.snapshot_hash, current_watermark(db)["record_seq"]


def _reproduce(client, sid):
    r = client.get(f"/api/snapshots/{sid}/reproduce", headers=H("actor-audit"))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_snapshot_under_its_own_engine_version_is_reproduced(client):
    from app import codes
    sid, recorded, _, _ = _seeded_snapshot()
    assert recorded == codes.ROLLUP_ENGINE_VERSION  # the seed was computed by this runtime's engine
    rep = _reproduce(client, sid)
    assert (rep["status"], rep["reproducible"]) == ("REPRODUCED", True)
    assert rep["rollup_engine_version"] == {"recorded": recorded, "running": codes.ROLLUP_ENGINE_VERSION}


def test_another_engine_version_is_reported_and_never_claimed_as_a_reproduction(client, monkeypatch):
    from app import codes
    from app.db import AdminSession
    from app.models import AssuranceSnapshot
    from app.platform import current_watermark
    sid, recorded, snap_hash, seq = _seeded_snapshot()
    monkeypatch.setattr(codes, "ROLLUP_ENGINE_VERSION", "ZA-STA-001:rollup-probe-next")
    rep = _reproduce(client, sid)
    assert rep["status"] == "ENGINE_CHANGED" and rep["reproducible"] is False
    assert rep["rollup_engine_version"] == {"recorded": recorded, "running": "ZA-STA-001:rollup-probe-next"}
    assert rep["dependency_digest_match"] is True  # the inputs are the same; only the engine differs
    with AdminSession() as db:  # the stored snapshot is untouched and nothing was recorded
        s = db.get(AssuranceSnapshot, sid)
        assert (s.rollup_engine_version, s.snapshot_hash) == (recorded, snap_hash)
        assert current_watermark(db)["record_seq"] == seq
