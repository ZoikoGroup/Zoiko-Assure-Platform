"""Catalogue cursor predicates: GET /catalog/requirements and GET /catalog/inbox return exactly the rows the previous
predicates returned, at every cursor boundary.

  * /requirements: rows after the cursor in (requirement_key, record_seq, requirement_version_id) order, now one SQL
    row-value comparison instead of "k > K OR (k = K AND s > S) OR (k = K AND s = S AND id > I)". The three columns are
    NOT NULL, so the two are the same set of rows; the row-value form lets the planner seek on the requirement_key index.
  * /inbox: order record_seq descending, source_id ascending; the predicate gains record_seq <= S (S: the cursor
    position's record sequence), which the disjunction already implies, so no row it admits is excluded.

The reference is a test-only copy of the previous predicates, evaluated by the same database (so collation is the
database's for both). Every check runs on the route functions inside a transaction that is rolled back. Runs on SQLite
and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import and_, event, or_, select

TAG = secrets.token_hex(3).lower()
AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
CONTENT = "actor-content-analyst"
KEYS = ["K-a", "K-A", "K_b", "K-10", "K-9", "K-a-1", "k-a"]  # case, punctuation and digit ordering are the database's


@contextmanager
def _session():
    from app.db import AdminSession
    with AdminSession() as db:
        try:
            yield db
        finally:
            db.rollback()


def _ctx(db):
    from app.models import Actor
    from app.security import Ctx
    return Ctx(db=db, actor=db.get(Actor, CONTENT))


def _seq(db):
    from app.platform import next_record_seq
    return next_record_seq(db)


# ------------------------------------------------------------------------------------------------ requirements
class _Reqs:
    def __init__(self, db):
        from app.models import Instrument, RegulatorySource
        from app.platform import sha256_hex
        self.db, self.p = db, f"cur-{TAG}"
        self.inst = Instrument(instrument_id=f"{self.p}-i", identifier=f"{self.p}-I", title="cursor probe",
                               jurisdiction="EU", authority="A", coverage_mode="ENCODED")
        self.src = RegulatorySource(source_id=f"{self.p}-s", authority="A", jurisdiction="EU", source_type="GUIDANCE",
                                    title="probe", locator=f"{self.p}-loc", fingerprint=sha256_hex("t"),
                                    rights_class="OFFICIAL_REUSE_PERMITTED", raw_text="t", lifecycle="CLASSIFIED",
                                    fetched_at=AT, record_seq=_seq(db))
        db.add_all([self.inst, self.src])
        db.flush()

    def add(self, rid, key, seq, lifecycle="PUBLISHED"):
        from app.models import RequirementVersion
        self.db.add(RequirementVersion(requirement_version_id=f"{self.p}-{rid}", requirement_key=f"{self.p}-{key}",
                                       instrument_id=self.inst.instrument_id, source_id=self.src.source_id, title="t",
                                       locator="Art. 1", valid_from=date(2025, 1, 1), lifecycle=lifecycle,
                                       record_seq=seq))
        self.db.flush()

    def build(self):
        """Ties on (key, record_seq) broken by ID only; one record_seq shared across keys; IDs inserted out of order."""
        tie = _seq(self.db)
        n = 0
        for k, key in enumerate(KEYS):
            for v, seq in enumerate((tie, tie, _seq(self.db))):  # two versions at the same record_seq, one later
                n += 1
                self.add(f"{(n * 7919) % 1000:03d}-{v}", key, seq, ("PUBLISHED", "VERIFIED")[(k + v) % 2])
        return n


def _req_filters(inst, limit, lifecycle=None):
    return {"instrument": inst, "requirement_key": None, "lifecycle": lifecycle, "origin": None, "source_id": None,
            "jurisdiction": None, "valid_at": None, "limit": limit, "sort": "requirement_key,record_seq,id"}


def _req_page(db, inst, limit, cursor=None, lifecycle=None):
    from app.routers import catalog as r
    return r.list_requirements(instrument=inst, requirement_key=None, lifecycle=lifecycle, origin=None, source_id=None,
                               jurisdiction=None, valid_at=None, limit=limit, cursor=cursor, ctx=_ctx(db))


def _req_reference(db, inst, pinned, after, limit, lifecycle=None):
    """The previous predicate, verbatim: rows after `after` = (key, seq, id) in (key, seq, id) order."""
    from app.models import Instrument, RequirementVersion as R
    q = select(R.requirement_version_id, R.requirement_key, R.record_seq).join(
        Instrument, Instrument.instrument_id == R.instrument_id).where(Instrument.identifier == inst)
    if lifecycle:
        q = q.where(R.lifecycle == lifecycle)
    if after is not None:
        k, s, i = after
        q = q.where(or_(R.requirement_key > k, and_(R.requirement_key == k, R.record_seq > s),
                        and_(R.requirement_key == k, R.record_seq == s, R.requirement_version_id > i)))
    q = q.where(R.record_seq <= pinned).order_by(R.requirement_key, R.record_seq, R.requirement_version_id)
    return db.execute(q.limit(limit + 1)).all()


def _req_traversal_matches(db, x, limit, lifecycle=None):
    from app.platform import current_watermark
    pinned = current_watermark(db)["record_seq"]
    body, after, got, want = _req_page(db, x.inst.identifier, limit, lifecycle=lifecycle), None, [], []
    while True:
        ref = _req_reference(db, x.inst.identifier, pinned, after, limit, lifecycle)
        assert [i["requirement_version_id"] for i in body["items"]] == [r.requirement_version_id for r in ref[:limit]]
        assert body["page"]["has_more"] == (len(ref) > limit)
        got += [i["requirement_version_id"] for i in body["items"]]
        want += [r.requirement_version_id for r in ref[:limit]]
        if not body["page"]["has_more"]:
            return got
        last = ref[limit - 1]
        after = (last.requirement_key, last.record_seq, last.requirement_version_id)
        body = _req_page(db, x.inst.identifier, limit, body["page"]["next_cursor"], lifecycle)


def test_requirements_pages_equal_the_previous_predicate_at_every_page_size(client):
    with _session() as db:
        x = _Reqs(db)
        n = x.build()
        orders = {limit: _req_traversal_matches(db, x, limit) for limit in (1, 2, 3, 5, n, n + 5)}
        assert len(orders[1]) == n and all(o == orders[1] for o in orders.values())  # one order, no gaps or repeats
        filtered = _req_traversal_matches(db, x, 2, lifecycle="VERIFIED")
        assert 0 < len(filtered) < n


def test_requirements_cursor_at_every_row_including_first_and_last(client):
    from app import pagination
    from app.platform import current_watermark
    with _session() as db:
        x = _Reqs(db)
        x.build()
        pinned = current_watermark(db)["record_seq"]
        rows = _req_reference(db, x.inst.identifier, pinned, None, 1000)
        fp = pagination.query_fingerprint(_req_filters(x.inst.identifier, 3))
        for row in rows:
            pos = [row.requirement_key, row.record_seq, row.requirement_version_id]
            cursor = pagination.encode_cursor(scope="catalog-requirements", query=fp, position=pos, watermark=pinned)
            page = _req_page(db, x.inst.identifier, 3, cursor)
            ref = _req_reference(db, x.inst.identifier, pinned, tuple(pos), 3)
            assert [i["requirement_version_id"] for i in page["items"]] == [r.requirement_version_id for r in ref[:3]]
            assert page["page"]["has_more"] == (len(ref) > 3)
        assert page["items"] == [] and page["page"]["has_more"] is False  # after the last row: an empty page


def test_requirements_pages_keep_the_pinned_watermark(client):
    with _session() as db:
        x = _Reqs(db)
        x.build()
        first = _req_page(db, x.inst.identifier, 4)
        x.add("late", "K-a", _seq(db))  # recorded after the first page, sorting into the middle of the order
        seen, body = [i["requirement_version_id"] for i in first["items"]], first
        while body["page"]["has_more"]:
            body = _req_page(db, x.inst.identifier, 4, body["page"]["next_cursor"])
            seen += [i["requirement_version_id"] for i in body["items"]]
        assert f"{x.p}-late" not in seen and len(seen) == len(set(seen))
        assert f"{x.p}-late" in [i["requirement_version_id"] for i in _req_page(db, x.inst.identifier, 200)["items"]]


# ------------------------------------------------------------------------------------------------ inbox
class _Inbox:
    def __init__(self, db):
        self.db, self.p, self.auth = db, f"curi-{TAG}", f"Cursor Probe {TAG}"

    def add(self, sid, seq, jurisdiction="EU"):
        from app.models import RegulatorySource
        from app.platform import sha256_hex
        self.db.add(RegulatorySource(source_id=f"{self.p}-{sid}", authority=self.auth, jurisdiction=jurisdiction,
                                     source_type="GUIDANCE", title="probe", locator=f"{self.p}-loc-{sid}",
                                     fingerprint=sha256_hex(sid), rights_class="OFFICIAL_REUSE_PERMITTED", raw_text=sid,
                                     lifecycle="CLASSIFIED", fetched_at=AT, record_seq=seq))
        self.db.flush()

    def build(self):
        """Groups of snapshots sharing a record_seq (source_id breaks the tie), inserted out of ID order."""
        n = 0
        for g in range(5):
            seq = _seq(self.db)
            for j in (3, 0, 2, 1)[: 1 + g % 4]:
                n += 1
                self.add(f"{g}-{j}", seq, ("EU", "UK")[(g + j) % 2])
        return n


def _inbox_filters(auth, limit, jurisdiction=None):
    return {"lifecycle": None, "requirement_lifecycle": None, "instrument": None, "jurisdiction": jurisdiction,
            "authority": auth, "limit": limit, "sort": "-record_seq,source_id"}


def _inbox_page(db, auth, limit, cursor=None, jurisdiction=None):
    from app.routers import catalog as r
    return r.regulatory_change_inbox(lifecycle=None, requirement_lifecycle=None, instrument=None,
                                     jurisdiction=jurisdiction, authority=auth, limit=limit, cursor=cursor, ctx=_ctx(db))


def _inbox_reference(db, auth, pinned, after, limit, jurisdiction=None):
    """The previous predicate, verbatim: rows after `after` = (seq, id) in (record_seq DESC, source_id) order."""
    from app.models import RegulatorySource as S
    q = select(S.source_id, S.record_seq).where(S.record_seq <= pinned, S.authority == auth)
    if jurisdiction:
        q = q.where(S.jurisdiction == jurisdiction)
    if after is not None:
        s, i = after
        q = q.where(or_(S.record_seq < s, and_(S.record_seq == s, S.source_id > i)))
    return db.execute(q.order_by(S.record_seq.desc(), S.source_id).limit(limit + 1)).all()


def _inbox_traversal_matches(db, x, limit, jurisdiction=None):
    from app.platform import current_watermark
    pinned = current_watermark(db)["record_seq"]
    body, after, got = _inbox_page(db, x.auth, limit, jurisdiction=jurisdiction), None, []
    while True:
        ref = _inbox_reference(db, x.auth, pinned, after, limit, jurisdiction)
        assert [i["source"]["source_id"] for i in body["items"]] == [r.source_id for r in ref[:limit]]
        assert body["page"]["has_more"] == (len(ref) > limit)
        got += [i["source"]["source_id"] for i in body["items"]]
        if not body["page"]["has_more"]:
            return got
        after = (ref[limit - 1].record_seq, ref[limit - 1].source_id)
        body = _inbox_page(db, x.auth, limit, body["page"]["next_cursor"], jurisdiction)


def test_inbox_pages_equal_the_previous_predicate_at_every_page_size(client):
    with _session() as db:
        x = _Inbox(db)
        n = x.build()
        orders = {limit: _inbox_traversal_matches(db, x, limit) for limit in (1, 2, 3, n, n + 2)}
        assert len(orders[1]) == n and all(o == orders[1] for o in orders.values())
        uk = _inbox_traversal_matches(db, x, 2, jurisdiction="UK")
        assert 0 < len(uk) < n


def test_inbox_cursor_at_every_row_including_first_and_last(client):
    from app import pagination
    from app.platform import current_watermark
    with _session() as db:
        x = _Inbox(db)
        x.build()
        pinned = current_watermark(db)["record_seq"]
        rows = _inbox_reference(db, x.auth, pinned, None, 1000)
        fp = pagination.query_fingerprint(_inbox_filters(x.auth, 2))
        for row in rows:
            cursor = pagination.encode_cursor(scope="catalog-inbox", query=fp, position=[row.record_seq, row.source_id],
                                              watermark=pinned)
            page = _inbox_page(db, x.auth, 2, cursor)
            ref = _inbox_reference(db, x.auth, pinned, (row.record_seq, row.source_id), 2)
            assert [i["source"]["source_id"] for i in page["items"]] == [r.source_id for r in ref[:2]]
            assert page["page"]["has_more"] == (len(ref) > 2)
        assert page["items"] == [] and page["page"]["has_more"] is False


def test_inbox_pages_keep_the_pinned_watermark(client):
    with _session() as db:
        x = _Inbox(db)
        x.build()
        first = _inbox_page(db, x.auth, 2)
        x.add("late", _seq(db))  # newer than every row: it would head a fresh first page
        seen, body = [i["source"]["source_id"] for i in first["items"]], first
        while body["page"]["has_more"]:
            body = _inbox_page(db, x.auth, 2, body["page"]["next_cursor"])
            seen += [i["source"]["source_id"] for i in body["items"]]
        assert f"{x.p}-late" not in seen and len(seen) == len(set(seen))
        assert _inbox_page(db, x.auth, 2)["items"][0]["source"]["source_id"] == f"{x.p}-late"


# ------------------------------------------------------------------------------------------------ the SQL itself
@pytest.mark.parametrize("which", ["requirements", "inbox"])
def test_cursor_pages_issue_the_seekable_predicate_and_no_extra_statement(client, which):
    from app.db import CELL_ID
    from app.models import Actor, RecordSequence
    with _session() as db:
        stmts: list[str] = []
        bind = db.connection()
        # Held for the whole test: the caller and the record-sequence row are then found in the session (its identity
        # map holds rows weakly), so both pages are compared on the statements the page itself issues.
        held = (db.get(Actor, CONTENT), db.get(RecordSequence, CELL_ID))

        def hook(conn, cursor, statement, *a):
            stmts.append(" ".join(statement.split()))
        if which == "requirements":
            x = _Reqs(db)
            x.build()
            first = _req_page(db, x.inst.identifier, 2)
            event.listen(bind, "before_cursor_execute", hook)
            try:
                n_first = len(stmts)
                _req_page(db, x.inst.identifier, 2)
                n_first = len(stmts) - n_first
                stmts.clear()
                _req_page(db, x.inst.identifier, 2, first["page"]["next_cursor"])
            finally:
                event.remove(bind, "before_cursor_execute", hook)
            page_sql = next(s for s in stmts if "FROM catalog_requirement_version JOIN" in s)
            assert ("(catalog_requirement_version.requirement_key, catalog_requirement_version.record_seq, "
                    "catalog_requirement_version.requirement_version_id) >") in page_sql
            assert " OR " not in page_sql.split(" WHERE ", 1)[1]
        else:
            x = _Inbox(db)
            x.build()
            first = _inbox_page(db, x.auth, 2)
            event.listen(bind, "before_cursor_execute", hook)
            try:
                n_first = len(stmts)
                _inbox_page(db, x.auth, 2)
                n_first = len(stmts) - n_first
                stmts.clear()
                _inbox_page(db, x.auth, 2, first["page"]["next_cursor"])
            finally:
                event.remove(bind, "before_cursor_execute", hook)
            page_sql = next(s for s in stmts if s.startswith("SELECT catalog_source_record.source_id") and "LIMIT" in s)
            where = page_sql.split(" WHERE ", 1)[1]
            assert where.count("catalog_source_record.record_seq <=") == 2  # pinned watermark and cursor position
        assert len(stmts) == n_first  # a cursor page issues as many statements as a first page
        del held
