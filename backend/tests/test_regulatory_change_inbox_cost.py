"""Regulatory Change Inbox cost and equivalence guards (GET /api/catalog/inbox; engines/catalog.change_inbox_items).

The inbox relates each page item to two catalogue-wide sets (obligation definitions: provision links are a JSON list;
content releases: the binding is in the signed manifest), so both are read whole: no index or relational link narrows
them. What is pinned here:

  * the inbox reads only the columns it shows: never a release's signature, changelog or hash, a draft's AST, vectors
    or reports, a definition's satisfaction template, nor a source's stored text beyond the page's own snapshots;
  * a fixed number of statements, whatever the number of items on the page or the size of the catalogue;
  * every relation keeps its catalogue order when several records qualify: releases (record sequence, ID), definitions
    (key, ID), pending drafts (definition, ID), snapshots (record sequence, ID); malformed manifest bindings are
    ignored; releases recorded after the pinned watermark and PUBLISHED drafts are left out.

Engine-level cases run inside a transaction that is rolled back: nothing they write reaches the shared catalogue (a
committed release with a synthetic manifest would fail the catalogue-wide release verification other modules check).
Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from sqlalchemy import event

from conftest import H

TAG = secrets.token_hex(3).lower()
AUTHOR = "actor-content-analyst"
AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _statements(bind, fn):
    out: list[str] = []

    def hook(conn, cursor, statement, *args):
        out.append(" ".join(statement.split()))
    event.listen(bind, "before_cursor_execute", hook)
    try:
        result = fn()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return out, result


class _Catalogue:
    """Synthetic catalogue rows written in the caller's transaction (rolled back by the test)."""

    def __init__(self, db, name: str):
        from app.models import Instrument
        self.db, self.p = db, f"cost-{TAG}-{name}"
        self.auth = f"Cost Probe {TAG} {name}"
        self.inst = Instrument(instrument_id=f"{self.p}-inst", identifier=f"{self.p}-INST", title="Cost probe",
                               jurisdiction="EU", authority=self.auth, coverage_mode="ENCODED")
        db.add(self.inst)
        db.flush()

    def seq(self) -> int:
        from app.platform import next_record_seq
        return next_record_seq(self.db)

    def source(self, sid, *, locator=None, text="Entities shall keep a log.", seq=None):
        from app.models import RegulatorySource
        from app.platform import sha256_hex
        s = RegulatorySource(source_id=f"{self.p}-{sid}", authority=self.auth, jurisdiction="EU", source_type="GUIDANCE",
                             title=sid, locator=locator or f"{self.p}-loc-{sid}", fingerprint=sha256_hex(text),
                             rights_class="OFFICIAL_REUSE_PERMITTED", raw_text=text, lifecycle="CLASSIFIED",
                             fetched_at=AT, record_seq=seq if seq is not None else self.seq())
        self.db.add(s)
        self.db.flush()
        return s

    def requirement(self, rid, src, *, provisions=2, release_id=None):
        from app.models import Provision, RequirementVersion
        rv = RequirementVersion(requirement_version_id=f"{self.p}-{rid}", requirement_key=f"{self.p}-{rid}",
                                instrument_id=self.inst.instrument_id, source_id=src.source_id, title=rid,
                                locator="Art. 1", valid_from=date(2025, 1, 1), lifecycle="PUBLISHED",
                                content_release_id=release_id, record_seq=self.seq())
        self.db.add(rv)
        self.db.flush()
        pids = [f"{rv.requirement_version_id}-p{k}" for k in range(provisions)]
        for k, pid in enumerate(pids):
            self.db.add(Provision(provision_id=pid, requirement_version_id=rv.requirement_version_id, locator=f"Art. 1({k})",
                                  duty_bearer="FINANCIAL_ENTITY", modality="MUST", action="keep",
                                  regulated_object="log", normalized_meaning="Keep a log.", lifecycle="PUBLISHED"))
        self.db.flush()
        return rv, pids

    def definition(self, key, pids):
        from app.models import ObligationDefinition
        o = ObligationDefinition(obligation_definition_id=f"{self.p}-{key}", key=f"{self.p}-{key}", title=key,
                                 instrument_id=self.inst.instrument_id, provision_ids=pids, subject_type="LEGAL_ENTITY",
                                 satisfaction_template={"node_type": "CONTROL", "node_id": "c", "control_key": "C"})
        self.db.add(o)
        self.db.flush()
        return o

    def release(self, cid, requirement_versions, *, seq=None, corrects=None, change_class="EXECUTABLE_OUTCOME"):
        from app.models import ContentRelease
        r = ContentRelease(content_release_id=f"{self.p}-{cid}", label=cid, status="PUBLISHED",
                           manifest={"label": cid, "change_class": change_class,
                                     "requirement_versions": requirement_versions},
                           manifest_hash="0" * 64, signature="sig", signer_key_id="probe", changelog="probe",
                           shadow_summary=None, released_by="actor-release", published_at=AT,
                           cell_received_seq=seq if seq is not None else self.seq(), correction_of_release_id=corrects)
        self.db.add(r)
        self.db.flush()
        return r

    def draft(self, did, odef, status="VERIFIED", cites=()):
        from app.models import RuleDraft
        d = RuleDraft(draft_id=f"{self.p}-{did}", obligation_definition_id=odef.obligation_definition_id,
                      ast={"node_type": "ALL", "node_id": "r", "children": []}, golden_vectors=[{"vector_id": "v"}],
                      source_mappings=[{"requirement_version_id": c} for c in cites], valid_from=date(2025, 1, 1),
                      status=status, change_class="EXECUTABLE_OUTCOME", author_id=AUTHOR, updated_at=AT)
        self.db.add(d)
        self.db.flush()
        return d

    def page(self, limit=200):
        """The page the endpoint would build for this probe's authority (same query, same pinned watermark)."""
        from sqlalchemy import select

        from app.engines import catalog
        from app.models import RegulatorySource
        from app.platform import current_watermark
        pinned = current_watermark(self.db)["record_seq"]
        rows = self.db.execute(select(RegulatorySource).where(RegulatorySource.authority == self.auth,
                                                              RegulatorySource.record_seq <= pinned)
                               .order_by(RegulatorySource.record_seq.desc(), RegulatorySource.source_id)
                               .limit(limit)).scalars().all()
        return catalog.change_inbox_items(self.db, rows, pinned)


def _ref(c: _Catalogue, x):
    return f"{c.p}-{x}"


# ------------------------------------------------------------------------------------------------ columns read
def test_the_inbox_reads_only_the_columns_it_shows(client):
    from app import db as dbmod
    statements, r = _statements(dbmod.engine, lambda: client.get("/api/catalog/inbox", headers=H(AUTHOR),
                                                                 params={"limit": 20}))
    assert r.status_code == 200, r.text
    sql = "\n".join(statements)
    for column in ("catalog_content_release.signature", "catalog_content_release.changelog",
                   "catalog_content_release.manifest_hash", "catalog_rule_draft.ast", "catalog_rule_draft.golden_vectors",
                   "catalog_rule_draft.decision_table", "catalog_rule_draft.validation_report",
                   "catalog_rule_draft.vector_report", "catalog_obligation_definition.satisfaction_template"):
        assert column not in sql, column
    # The stored source text is read once: by the page query, for the page's own snapshots (fingerprint check); the
    # snapshot-chain lookup reads identity and digest only.
    assert sum("catalog_source_record.raw_text" in s for s in statements) == 1, statements
    assert any("catalog_content_release.manifest" in s for s in statements)  # the signed binding authority


# ------------------------------------------------------------------------------------------------ statement count
def test_the_statement_count_is_fixed_whatever_the_page_or_catalogue_size(client):
    from app.db import AdminSession
    with AdminSession() as db:
        c = _Catalogue(db, "count")
        bind = db.connection()
        try:
            def grow(start, n, background):
                for i in range(start, start + n):
                    s = c.source(f"s{i:03d}")
                    rv, pids = c.requirement(f"r{i:03d}", s)
                    c.release(f"cr{i:03d}", [{"requirement_version_id": rv.requirement_version_id}])
                    c.draft(f"d{i:03d}", c.definition(f"o{i:03d}", pids), cites=[rv.requirement_version_id])
                for j in range(background):  # catalogue rows no page item relates to
                    c.definition(f"bg-o-{start}-{j}", [f"bg-{start}-{j}"])
                    c.release(f"bg-cr-{start}-{j}", [{"requirement_version_id": f"bg-{start}-{j}"}])
            grow(0, 1, 0)
            one, items = _statements(bind, c.page)
            assert len(items) == 1
            grow(1, 24, 60)
            many, items = _statements(bind, c.page)
            assert len(items) == 25 and len(many) == len(one), (len(one), len(many))
        finally:
            db.rollback()


# ------------------------------------------------------------------------------------------------ relations and order
def test_relations_keep_catalogue_order_and_skip_what_the_page_must_not_show(client):
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        c = _Catalogue(db, "order")
        try:
            old = c.source("snap-1", locator=f"{c.p}-shared", text="Entities shall keep a log (2024).")
            first = c.source("snap-2", locator=f"{c.p}-shared")
            rv, pids = c.requirement("rv", first, release_id=_ref(c, "first"))
            other = c.source("other")
            rv2, pids2 = c.requirement("rv2", other)
            # Two definitions linking the page provisions (key order "a" < "b", inserted b first); one unrelated.
            ob = c.definition("b-def", [pids[1], pids[0], pids[0]])  # duplicate link entries are kept as linked
            oa = c.definition("a-def", [pids[0]])
            c.definition("unrelated", ["no-such-provision"])
            # Releases: first publication (named on the version), then three binding releases recorded in an order
            # different from their IDs, one with malformed binding entries, a correction, and one after the watermark.
            c.release("first", [], seq=c.seq())
            c.release("zz-binds", [{"requirement_version_id": rv.requirement_version_id}])
            c.release("aa-binds", ["not-an-entry", {"requirement_version_id": 7},
                                   {"requirement_version_id": rv.requirement_version_id}])
            c.release("malformed", {"requirement_version_id": rv.requirement_version_id})  # not a list: binds nothing
            c.release("corr", [{"requirement_version_id": rv.requirement_version_id}], corrects=_ref(c, "zz-binds"),
                      change_class="CORRECTION")
            c.release("unrelated", [{"requirement_version_id": rv2.requirement_version_id}])
            future_seq = current_watermark(db)["record_seq"] + 100
            c.release("future", [{"requirement_version_id": rv.requirement_version_id}], seq=future_seq)
            # Drafts: pending ones for both definitions (inserted out of ID order), a PUBLISHED one (never shown).
            c.draft("d-2", oa, cites=[rv.requirement_version_id, "elsewhere"])
            c.draft("d-1", oa, status="IN_REVIEW")
            c.draft("d-0", ob, status="DRAFT")
            c.draft("d-pub", oa, status="PUBLISHED")
            items = {i["source"]["source_id"]: i for i in c.page()}
            item = items[first.source_id]
            assert item["snapshot"] == {"number": 2, "of": 2, "previous_source_id": old.source_id,
                                        "fingerprint_changed": True}
            assert items[old.source_id]["snapshot"]["number"] == 1 and items[old.source_id]["requirement_versions"] == []
            [v] = item["requirement_versions"]
            assert v["content_release_id"] == _ref(c, "first")
            assert v["released_in"] == [_ref(c, "zz-binds"), _ref(c, "aa-binds"), _ref(c, "corr")]  # record order
            assert [r["content_release_id"] for r in item["content_releases"]] == [
                _ref(c, x) for x in ("first", "zz-binds", "aa-binds", "corr")]
            rels = {r["content_release_id"]: r for r in item["content_releases"]}
            assert rels[_ref(c, "first")]["binds_requirement_version_ids"] == []
            assert rels[_ref(c, "first")]["first_published_requirement_version_ids"] == [rv.requirement_version_id]
            assert rels[_ref(c, "zz-binds")]["corrected_by_release_ids"] == [_ref(c, "corr")]
            assert (rels[_ref(c, "corr")]["is_correction"], rels[_ref(c, "corr")]["change_class"]) == (True, "CORRECTION")
            assert rels[_ref(c, "aa-binds")]["shadow_impact"]["status"] == "NOT_RECORDED"
            assert [o["obligation_definition_id"] for o in item["obligation_definitions"]] == [oa.obligation_definition_id,
                                                                                               ob.obligation_definition_id]
            assert item["obligation_definitions"][1]["linked_provision_ids"] == [pids[1], pids[0], pids[0]]
            assert [d["draft_id"] for d in item["pending_rule_drafts"]] == [_ref(c, "d-1"), _ref(c, "d-2"), _ref(c, "d-0")]
            assert item["pending_rule_drafts"][1]["cites_requirement_version_ids"] == [rv.requirement_version_id]
            assert [r["content_release_id"] for r in items[other.source_id]["content_releases"]] == [_ref(c, "unrelated")]
            assert items[other.source_id]["obligation_definitions"] == []
        finally:
            db.rollback()
