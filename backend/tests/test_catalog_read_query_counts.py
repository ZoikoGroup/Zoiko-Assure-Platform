"""Regulatory catalogue read paths: a fixed number of SQL statements per request, whatever the number of rows they
list, with unchanged responses.

Each catalogue list used to issue one or more queries per row (the requirements page: a version-number read, a
source read and a provision count per item; instruments: two per instrument; obligation definitions: four per
definition; controls: one per control; releases: one per release, two for an organisation reader; the traceability
validation: one per definition and one per rule, which every requirement trace also ran; history, source and snapshot
views: per version). They now read each related table once per request and relate rows in memory, in the orders the
per-row queries had. Pinned here: the statement count does not change when rows are added, and the batched values
(version numbers, provision counts, in-force versions, linked provisions, correcting releases, an organisation's
evaluation counts) are those the per-row reads gave.

Everything runs inside a transaction that is rolled back, on the route functions themselves: no synthetic release or
rule version reaches the shared catalogue (catalogue-wide checks in other modules verify every committed release).
Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import event

TAG = secrets.token_hex(3).lower()
AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
CONTENT, ORG, ORG_TENANT = "actor-content-analyst", "actor-cco", "tenant-zoiko-group"


@contextmanager
def _session():
    from app.db import AdminSession
    with AdminSession() as db:
        try:
            yield db
        finally:
            db.rollback()


def _count(db, fn):
    n = []

    def hook(*a):
        n.append(1)
    bind = db.connection()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        out = fn()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return len(n), out


def _ctx(db, actor=CONTENT):
    from app.models import Actor
    from app.security import Ctx
    return Ctx(db=db, actor=db.get(Actor, actor))


class _Rows:
    """Synthetic catalogue rows in the caller's (rolled-back) transaction."""

    def __init__(self, db, name):
        self.db, self.p = db, f"rq-{TAG}-{name}"

    def seq(self):
        from app.platform import next_record_seq
        return next_record_seq(self.db)

    def instrument(self, k=0):
        from app.models import Instrument
        i = Instrument(instrument_id=f"{self.p}-i{k}", identifier=f"{self.p}-I{k}", title="probe", jurisdiction="EU",
                       authority="A", coverage_mode="ENCODED")
        self.db.add(i)
        self.db.flush()
        return i

    def source(self, k, locator=None):
        from app.models import RegulatorySource
        from app.platform import sha256_hex
        s = RegulatorySource(source_id=f"{self.p}-s{k}", authority="A", jurisdiction="EU", source_type="GUIDANCE",
                             title="probe", locator=locator or f"{self.p}-loc{k}", fingerprint=sha256_hex(f"t{k}"),
                             rights_class="OFFICIAL_REUSE_PERMITTED", raw_text=f"t{k}", lifecycle="CLASSIFIED",
                             fetched_at=AT, record_seq=self.seq())
        self.db.add(s)
        self.db.flush()
        return s

    def requirement(self, k, inst, src, key=None, provisions=1):
        from app.models import Provision, RequirementVersion
        rv = RequirementVersion(requirement_version_id=f"{self.p}-r{k}", requirement_key=key or f"{self.p}-K{k}",
                                instrument_id=inst.instrument_id, source_id=src.source_id, title="probe", locator="Art. 1",
                                valid_from=date(2025, 1, 1), lifecycle="PUBLISHED", record_seq=self.seq())
        self.db.add(rv)
        self.db.flush()
        pids = []
        for q in range(provisions):
            pids.append(f"{rv.requirement_version_id}-p{q}")
            self.db.add(Provision(provision_id=pids[-1], requirement_version_id=rv.requirement_version_id,
                                  locator=f"Art. 1({q})", duty_bearer="X", modality="MUST", action="a",
                                  regulated_object="o", normalized_meaning="m", lifecycle="PUBLISHED"))
        self.db.flush()
        return rv, pids

    def definition(self, k, inst, links):
        from app.models import ObligationDefinition
        o = ObligationDefinition(obligation_definition_id=f"{self.p}-o{k}", key=f"{self.p}-O{k}", title="probe",
                                 instrument_id=inst.instrument_id, provision_ids=links, subject_type="LEGAL_ENTITY")
        self.db.add(o)
        self.db.flush()
        return o

    def rule(self, k, odef, version, valid_from, valid_to=None, rule_ids=None, corrects=None):
        from app.models import ContentRelease, RuleVersion
        s = self.seq()
        cr = ContentRelease(content_release_id=f"{self.p}-cr{k}", label=f"{self.p}-{k}", status="PUBLISHED",
                            manifest={"rule_versions": [{"rule_version_id": r} for r in (rule_ids or [f"{self.p}-rv{k}"])]},
                            manifest_hash="0" * 64, signature="s", signer_key_id="k", released_by="actor-release",
                            published_at=AT, cell_received_seq=s, correction_of_release_id=corrects)
        self.db.add(cr)
        if odef is not None:
            self.db.add(RuleVersion(rule_version_id=f"{self.p}-rv{k}", obligation_definition_id=odef.obligation_definition_id,
                                    version=version, valid_from=valid_from, valid_to=valid_to, rule_schema_version="1.0",
                                    operator_set_version="1.0", canonical_ast={}, ast_hash="0" * 64, required_facts=[],
                                    status="PUBLISHED", content_release_id=cr.content_release_id, record_seq=s))
        self.db.flush()
        return cr

    def control(self, k, mappings):
        from app.models import ControlDefinition, FrameworkMapping
        c = ControlDefinition(control_definition_id=f"{self.p}-c{k}", key=f"{self.p}-C{k}", title="probe", objective="o",
                              category="X")
        self.db.add(c)
        self.db.flush()
        for f, ref in mappings:
            self.db.add(FrameworkMapping(control_definition_id=c.control_definition_id, framework=f, reference=ref))
        self.db.flush()
        return c


# ------------------------------------------------------------------------------------------------ requirements page
def test_the_requirements_page_reads_a_fixed_number_of_statements(client):
    from app.routers import catalog as r
    with _session() as db:
        x = _Rows(db, "page")
        inst, src = x.instrument(), x.source(0)
        other = x.source(1)
        for k in range(12):  # three versions of one key across two snapshots, the rest single versions; 0-2 provisions
            x.requirement(k, inst, other if k == 1 else src, key=f"{x.p}-SHARED" if k < 3 else None, provisions=k % 3)

        def page(limit):
            return r.list_requirements(instrument=inst.identifier, requirement_key=None, lifecycle=None, origin=None,
                                       source_id=None, jurisdiction=None, valid_at=None, limit=limit, cursor=None,
                                       ctx=_ctx(db))
        one, _ = _count(db, lambda: page(1))
        twelve, body = _count(db, lambda: page(50))
        assert len(body["items"]) == 12 and twelve == one, (one, twelve)
        items = {i["requirement_version_id"]: i for i in body["items"]}
        assert [items[f"{x.p}-r{k}"]["version_number"] for k in range(3)] == [1, 2, 3]  # record order within the key
        assert [items[f"{x.p}-r{k}"]["provision_count"] for k in range(4)] == [0, 1, 2, 0]
        assert items[f"{x.p}-r1"]["source"] == {"source_id": other.source_id, "fingerprint": other.fingerprint,
                                                 "locator": other.locator, "lifecycle": "CLASSIFIED"}
        assert items[f"{x.p}-r5"]["instrument"]["identifier"] == inst.identifier


# ------------------------------------------------------------------------------------------------ catalogue lists
def test_catalogue_lists_do_not_grow_per_row(client):
    from app.routers import catalog as r
    lists = {"instruments": lambda db: r.instruments(ctx=_ctx(db)),
             "obligation definitions": lambda db: r.odefs(ctx=_ctx(db)),
             "controls": lambda db: r.control_defs(ctx=_ctx(db)),
             "releases (content)": lambda db: r.releases(ctx=_ctx(db)),
             "releases (organisation)": lambda db: r.releases(ctx=_ctx(db, ORG))}
    with _session() as db:
        x = _Rows(db, "lists")
        before = {k: _count(db, lambda f=f: f(db))[0] for k, f in lists.items()}
        for k in range(6):
            inst, src = x.instrument(k), x.source(k)
            rv, pids = x.requirement(k, inst, src, provisions=2)
            o = x.definition(k, inst, pids)
            x.rule(k, o, 1, date(2025, 1, 1))
            x.control(k, [("F1", f"R{k}"), ("F0", "R9")])
        after = {k: _count(db, lambda f=f: f(db))[0] for k, f in lists.items()}
        assert after == before, (before, after)


def test_batched_list_values_are_those_the_per_row_reads_gave(client):
    """In-force and latest versions with overlapping, future and expired periods; links in link order with duplicates,
    missing and non-string entries left out; correcting releases in record order; an organisation's evaluation count per
    release counting each evaluation once even when the manifest names its rule version twice; grouped lists in order."""
    from app.models import ApplicabilityEvaluation
    from app.routers import catalog as r
    with _session() as db:
        x = _Rows(db, "values")
        inst, src = x.instrument(), x.source(0)
        rv, pids = x.requirement(0, inst, src, provisions=3)
        o = x.definition(0, inst, [pids[2], pids[0], pids[2], "no-such-provision", 7])
        x.rule("a", o, 1, date(2024, 1, 1))                       # in force, overtaken
        x.rule("b", o, 2, date(2025, 1, 1))                       # in force today (highest in force)
        x.rule("c", o, 3, date(2099, 1, 1))                       # latest, not yet in force
        x.rule("d", o, 4, date(2020, 1, 1), date(2021, 1, 1))     # latest of all, expired
        first = x.rule("e", None, 0, None, rule_ids=[f"{x.p}-rva", f"{x.p}-rva"])  # names one rule version twice
        x.rule("f", None, 0, None, rule_ids=[], corrects=first.content_release_id)
        x.rule("g", None, 0, None, rule_ids=[], corrects=first.content_release_id)
        db.add(ApplicabilityEvaluation(tenant_id=ORG_TENANT, subject_id="subj-entity-eu",
                                       obligation_definition_id=o.obligation_definition_id, rule_version_id=f"{x.p}-rva",
                                       effective_at=AT, record_cutoff_seq=1, evaluator_build="b", input_bundle={},
                                       input_bundle_hash="h", evaluation_key=f"{x.p}-key", outcome="APPLIES", trace={},
                                       trigger="EXPLICIT", requested_by=ORG, record_seq=x.seq()))
        x.control(0, [("F1", "R2"), ("F0", "R9"), ("F0", "R1")])
        db.flush()
        [d] = [d for d in r.odefs(ctx=_ctx(db)) if d["obligation_definition_id"] == o.obligation_definition_id]
        assert (d["current_rule_version"]["rule_version_id"], d["current_rule_version"]["effective_status"]) == (
            f"{x.p}-rvd", "NO_LONGER_EFFECTIVE")
        assert d["effective_rule_version"]["rule_version_id"] == f"{x.p}-rvb"
        assert [p["provision_id"] for p in d["provisions"]] == [pids[2], pids[0]]
        [i] = [i for i in r.instruments(ctx=_ctx(db)) if i["instrument_id"] == inst.instrument_id]
        assert ([o["obligation_definition_id"] for o in i["obligation_definitions"]],
                [q["requirement_version_id"] for q in i["requirements"]]) == ([o.obligation_definition_id], [rv.requirement_version_id])
        [c] = [c for c in r.control_defs(ctx=_ctx(db)) if c["key"] == f"{x.p}-C0"]
        assert [(m["framework"], m["reference"]) for m in c["framework_mappings"]] == [("F0", "R1"), ("F0", "R9"), ("F1", "R2")]
        org = {e["content_release_id"]: e for e in r.releases(ctx=_ctx(db, ORG))}
        assert org[first.content_release_id]["corrected_by_release_ids"] == [f"{x.p}-crf", f"{x.p}-crg"]
        assert org[first.content_release_id]["tenant_impacted_evaluations"] == 1
        assert org[f"{x.p}-crf"]["tenant_impacted_evaluations"] == 0
        content = {e["content_release_id"]: e for e in r.releases(ctx=_ctx(db))}
        assert "tenant_impacted_evaluations" not in content[first.content_release_id]


# ------------------------------------------------------------------------------------------------ validation and traces
def test_validation_and_requirement_trace_do_not_grow_with_the_catalogue(client):
    from app.engines import catalog
    from app.routers import catalog as r
    with _session() as db:
        x = _Rows(db, "validate")
        inst, src = x.instrument(), x.source(0)
        rv, pids = x.requirement(0, inst, src, provisions=1)
        x.rule(0, x.definition(0, inst, pids), 1, date(2025, 1, 1))
        before = (_count(db, lambda: catalog.validate(db))[0],
                  _count(db, lambda: r.requirement_trace(rv.requirement_version_id, ctx=_ctx(db)))[0])
        for k in range(1, 7):  # more catalogue content elsewhere: definitions with released rules (one query each before)
            other_rv, other_pids = x.requirement(k, inst, src, provisions=1)
            x.rule(k, x.definition(k, inst, other_pids), 1, date(2025, 1, 1))
        after = (_count(db, lambda: catalog.validate(db))[0],
                 _count(db, lambda: r.requirement_trace(rv.requirement_version_id, ctx=_ctx(db)))[0])
        assert after == before, (before, after)
        # The trace still runs the whole validation (its issues are filtered to this version) and still lists only the
        # definitions linking this version's provisions.
        trace = r.requirement_trace(rv.requirement_version_id, ctx=_ctx(db))
        assert [o["obligation_definition_id"] for o in trace["obligation_definitions"]] == [f"{x.p}-o0"]


# ------------------------------------------------------------------------------------------------ per-version views
def test_history_source_and_snapshot_views_do_not_grow_per_version(client):
    from app.routers import catalog as r
    with _session() as db:
        x = _Rows(db, "versions")
        inst = x.instrument()
        srcs = [x.source(0, locator=f"{x.p}-shared")]
        x.requirement(0, inst, srcs[0], key=f"{x.p}-K", provisions=2)
        rid = f"{x.p}-r0"

        def views():
            return (_count(db, lambda: r.requirement_history(rid, ctx=_ctx(db)))[0],
                    _count(db, lambda: r.source(srcs[0].source_id, ctx=_ctx(db)))[0],
                    _count(db, lambda: r.source_versions(srcs[0].source_id, ctx=_ctx(db)))[0])
        before = views()
        for k in range(1, 5):  # more snapshots of the source, more versions of the key on the first snapshot
            srcs.append(x.source(k, locator=f"{x.p}-shared"))
            x.requirement(k, inst, srcs[0] if k % 2 else srcs[k], key=f"{x.p}-K", provisions=k % 3)
        assert views() == before
        h = r.requirement_history(rid, ctx=_ctx(db))
        assert [v["version_number"] for v in h["versions"]] == [1, 2, 3, 4, 5]
        assert [len(v["provisions"]) for v in h["versions"]] == [2, 1, 2, 0, 1]
        snaps = r.source_versions(srcs[0].source_id, ctx=_ctx(db))["snapshots"]
        assert [s["requirement_versions"] for s in snaps] == [3, 0, 1, 0, 1]
        assert [v["version_number"] for v in r.source(srcs[0].source_id, ctx=_ctx(db))["requirement_versions"]] == [1, 2, 4]
