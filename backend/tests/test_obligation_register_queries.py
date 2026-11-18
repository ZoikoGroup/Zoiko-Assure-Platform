"""GET /obligations reads each related table once per request, never once per obligation, with unchanged responses.

The register used to read, for every current obligation revision, its definition, its subject, its latest assurance
snapshot (the whole row, segment trace and dependency manifest included, then dropped) and a count of its approved
exceptions: up to four statements per obligation. It now reads each table once for the register and relates the rows in
memory. Pinned here:
  * the response is the one the per-row reads gave (a test-only copy of them, run on the same data), key order included,
    across the cases they handled: no obligation; a definition or subject that cannot be found; no snapshot; snapshots
    tied on created_at (the snapshot ID decides, descending); closed earlier revisions (not listed); APPROVED and other
    exception states; another organisation's rows naming the same obligation lineage;
  * the number of SQL statements does not grow with the number of obligations;
  * the order stays the record order (record_from_seq, then revision ID), ties included;
  * at the API, an organisation sees only its own obligations and counts, and a caller without dossier read is refused.

Synthetic rows are written inside a transaction that is rolled back and the route function is called on it; the API
checks use their own committed organisations. Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import json
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
AT = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=timezone.utc)
LARGE = 400


@pytest.fixture(autouse=True, scope="module")
def _app(client):
    """The schema and the seeded catalogue (obligation definitions) exist once the application has started."""
    return client


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


def _reference(db, tenant):
    """The register as the per-row reads built it (the route before this change), for comparison only."""
    from app.models import AssuranceSnapshot, ExceptionRecord, ObligationDefinition, ObligationInstance, Subject
    from app.platform import to_dict
    from app.routers.assurance import _missing
    out = []
    for o in db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant,
                                                         ObligationInstance.record_to_seq.is_(None))
                        .order_by(ObligationInstance.record_from_seq, ObligationInstance.revision_id)).scalars():
        d = to_dict(o)
        od = db.get(ObligationDefinition, o.obligation_definition_id)
        subject = db.get(Subject, o.subject_id)
        d["definition"] = ({"key": od.key, "title": od.title, "schedule_kind": od.schedule_kind} if od else
                           _missing("obligation_definition_id", o.obligation_definition_id, "key", "title", "schedule_kind"))
        d["subject"] = to_dict(subject) if subject else _missing("subject_id", o.subject_id, "display_name")
        snap = db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == tenant,
                                                          AssuranceSnapshot.obligation_lineage_id == o.lineage_id)
                          .order_by(AssuranceSnapshot.created_at.desc(), AssuranceSnapshot.snapshot_id.desc())
                          ).scalars().first()
        d["latest_snapshot"] = {k: v for k, v in to_dict(snap).items() if k not in ("segments", "dependency_manifest")} if snap else None
        d["open_exceptions"] = db.query(ExceptionRecord).filter(ExceptionRecord.tenant_id == tenant,
                                                                ExceptionRecord.target_id == o.lineage_id,
                                                                ExceptionRecord.state == "APPROVED").count()
        out.append(d)
    return out


def _register(db, tenant):
    from app.routers.assurance import obligations
    return obligations(ctx=SimpleNamespace(db=db, tenant=tenant))


def _measure(db, fn, tenant):
    """(statements, response) of one register read, with nothing of it already in the session."""
    db.expunge_all()
    return _count(db, lambda: fn(db, tenant))


def _same(new, old):
    """Equal values, in the same list order and the same key order."""
    assert new == old
    assert json.dumps(new, default=str) == json.dumps(old, default=str)


class _World:
    """Synthetic organisations, subjects, obligations, snapshots and exceptions in the caller's transaction."""

    def __init__(self, db, name):
        from app.models import ObligationDefinition
        self.db, self.p, self.k = db, f"olq-{TAG}-{name}", 0
        self.defs = list(db.execute(select(ObligationDefinition.obligation_definition_id)
                                    .order_by(ObligationDefinition.key).limit(3)).scalars())
        assert len(self.defs) == 3

    def _id(self, kind):
        self.k += 1
        return f"{self.p}-{kind}{self.k}"

    def seq(self):
        from app.platform import next_record_seq
        return next_record_seq(self.db)

    def tenant(self):
        from app.models import Tenant
        t = self._id("t")
        self.db.add(Tenant(tenant_id=t, legal_name=f"{t} Group", contracting_region="EU"))
        self.db.flush()
        return t

    def subject(self, tenant):
        from app.models import Subject
        s = self._id("s")
        self.db.add(Subject(subject_id=s, tenant_id=tenant, entity_id=f"{tenant}-e", subject_type="LEGAL_ENTITY",
                            display_name=f"{s} Ltd"))
        return s

    def obligation(self, tenant, subject, odef, *, seq=None, lifecycle="ADOPTED", closed_before=0, valid_to=None):
        from app.models import ObligationInstance
        lineage = self._id("l")
        common = dict(lineage_id=lineage, tenant_id=tenant, entity_id=f"{tenant}-e", subject_id=subject,
                      obligation_definition_id=odef, determination_revision_id=self._id("d"),
                      attestation_id=self._id("a"), owner_actor_id="probe", valid_from=AT)
        for _ in range(closed_before):  # earlier revisions of the lineage, closed in record time
            s = self.seq()
            self.db.add(ObligationInstance(revision_id=self._id("r"), lifecycle="ADOPTED", record_from_seq=s,
                                           record_to_seq=s + 1, **common))
        self.db.add(ObligationInstance(revision_id=self._id("r"), lifecycle=lifecycle, valid_to=valid_to,
                                       record_from_seq=seq if seq is not None else self.seq(), **common))
        return lineage

    def snapshot(self, tenant, lineage, at, *, state="PROVEN", snapshot_id=None):
        from app.models import AssuranceSnapshot
        sid = snapshot_id or self._id("snap")
        self.db.add(AssuranceSnapshot(
            snapshot_id=sid, tenant_id=tenant, entity_id=f"{tenant}-e", obligation_lineage_id=lineage,
            obligation_revision_id="probe", period_from=AT, period_to=AT + timedelta(days=30), record_cutoff_seq=7,
            perspective="CURRENT", expression_version_id="probe", rollup_engine_version="probe", state=state,
            segments=[{"segment_range": ["a", "b"], "trace": "x" * 200}], dependency_manifest={"big": ["y" * 200]},
            dependency_digest="d" * 64, snapshot_hash="h" * 64, exception_treatments=[], reason_codes=["R1"],
            created_by="probe", created_at=at))
        return sid

    def exception(self, tenant, target, state):
        from app.models import ExceptionRecord
        self.db.add(ExceptionRecord(exception_id=self._id("x"), tenant_id=tenant, target_type="OBLIGATION",
                                    target_id=target, reason="probe", state=state, requested_by="probe",
                                    valid_from=AT, valid_to=AT + timedelta(days=90), record_seq=self.seq()))

    def flush(self):
        self.db.flush()

    def register_rows(self, tenant, n, *, ties=False):
        """n obligations, each with snapshots (two tied on created_at when ties) and exceptions in several states."""
        subjects = [self.subject(tenant) for _ in range(max(1, n // 3))]
        lineages = []
        for i in range(n):
            lin = self.obligation(tenant, subjects[i % len(subjects)], self.defs[i % 3], closed_before=i % 2)
            lineages.append(lin)
            self.snapshot(tenant, lin, AT, state="NOT_PROVEN")
            if ties:
                self.snapshot(tenant, lin, AT + timedelta(days=1), snapshot_id=f"{lin}-snap-a", state="FAILING")
                self.snapshot(tenant, lin, AT + timedelta(days=1), snapshot_id=f"{lin}-snap-b", state="PROVEN")
            for state in ("APPROVED", "APPROVED", "PENDING_APPROVAL", "REVOKED")[: 1 + i % 4]:
                self.exception(tenant, lin, state)
        self.flush()
        return lineages


# ------------------------------------------------------------------------------------------------ equivalence
def test_an_empty_register_is_one_statement_and_an_empty_list():
    with _session() as db:
        w = _World(db, "empty")
        t = w.tenant()
        w.flush()
        n, got = _measure(db, _register, t)
        assert got == [] == _reference(db, t)
        assert n == 1


def test_the_register_is_the_per_row_result_on_mixed_data():
    with _session() as db:
        w = _World(db, "mixed")
        a, b = w.tenant(), w.tenant()
        sa, sb = w.subject(a), w.subject(b)
        missing_subject = f"{w.p}-no-such-subject"
        tied = w.obligation(a, sa, w.defs[0])
        w.snapshot(a, tied, AT, snapshot_id=f"{tied}-snap-1", state="FAILING")
        w.snapshot(a, tied, AT, snapshot_id=f"{tied}-snap-2", state="PROVEN")       # same created_at: the ID decides
        w.snapshot(a, tied, AT - timedelta(days=5), snapshot_id=f"{tied}-snap-9", state="NOT_PROVEN")
        no_snapshot = w.obligation(a, sa, w.defs[1], closed_before=2)               # two closed earlier revisions
        no_definition = w.obligation(a, sa, f"{w.p}-no-such-definition", lifecycle="CLOSED", valid_to=AT)
        no_subject = w.obligation(a, missing_subject, w.defs[2])
        for state in ("APPROVED", "APPROVED", "PENDING_APPROVAL", "REJECTED", "REVOKED"):
            w.exception(a, tied, state)
        w.exception(a, no_subject, "APPROVED")
        # Another organisation naming the same lineage: never counted, never shown.
        w.exception(b, tied, "APPROVED")
        w.snapshot(b, tied, AT + timedelta(days=99), snapshot_id=f"{tied}-snap-z", state="FAILING")
        other = w.obligation(b, sb, w.defs[0])
        w.flush()
        _, got = _measure(db, _register, a)
        _, old = _measure(db, _reference, a)
        _same(got, old)
        by = {o["lineage_id"]: o for o in got}
        assert set(by) == {tied, no_snapshot, no_definition, no_subject} and other not in by
        assert by[tied]["latest_snapshot"]["snapshot_id"] == f"{tied}-snap-2"
        assert "segments" not in by[tied]["latest_snapshot"] and "dependency_manifest" not in by[tied]["latest_snapshot"]
        assert by[tied]["open_exceptions"] == 2 and by[no_subject]["open_exceptions"] == 1
        assert by[no_snapshot]["latest_snapshot"] is None and by[no_snapshot]["open_exceptions"] == 0
        assert by[no_definition]["definition"] == {"obligation_definition_id": f"{w.p}-no-such-definition", "key": None,
                                                   "title": None, "schedule_kind": None, "missing": True}
        assert by[no_subject]["subject"] == {"subject_id": missing_subject, "display_name": None, "missing": True}
        assert len([o for o in got if o["lineage_id"] == no_snapshot]) == 1  # closed revisions are not listed
        _, got_b = _measure(db, _register, b)
        _same(got_b, _measure(db, _reference, b)[1])
        assert [o["lineage_id"] for o in got_b] == [other] and got_b[0]["open_exceptions"] == 0


def test_record_order_and_its_tie_break_are_kept():
    with _session() as db:
        w = _World(db, "order")
        t = w.tenant()
        s = w.subject(t)
        first = w.obligation(t, s, w.defs[0])
        tie = w.seq()  # later than first's
        late = w.obligation(t, s, w.defs[1], seq=tie + 10_000)
        tied = [w.obligation(t, s, w.defs[i % 3], seq=tie) for i in range(4)]  # one record sequence: revision ID decides
        w.flush()
        _, got = _measure(db, _register, t)
        _same(got, _measure(db, _reference, t)[1])
        tied_order = [o["lineage_id"] for o in sorted((o for o in got if o["lineage_id"] in tied),
                                                       key=lambda o: o["revision_id"])]
        assert [o["lineage_id"] for o in got] == [first, *tied_order, late]


# ------------------------------------------------------------------------------------------------ statement counts
@pytest.mark.parametrize("n", [1, 5, 25])
def test_the_statement_count_does_not_grow_with_the_register(n):
    with _session() as db:
        w = _World(db, f"count{n}")
        t = w.tenant()
        w.register_rows(t, n, ties=True)
        statements, got = _measure(db, _register, t)
        before, old = _measure(db, _reference, t)
        _same(got, old)
        assert len(got) == n
        assert statements == 5, statements  # obligations, definitions, subjects, latest snapshots, exception counts
        assert before >= 1 + 2 * n          # the per-row reads: at least a snapshot and a count per obligation


def test_a_larger_register_is_the_same_and_stays_at_five_statements(capsys):
    with _session() as db:
        w = _World(db, "large")
        t = w.tenant()
        w.register_rows(t, LARGE, ties=True)
        timings = {}
        for name, fn in (("batched", _register), ("per_row", _reference)):
            runs = []
            for _ in range(3):
                start = time.perf_counter()
                statements, out = _measure(db, fn, t)
                runs.append(time.perf_counter() - start)
            timings[name] = (statements, min(runs), out)
        _same(timings["batched"][2], timings["per_row"][2])
        assert timings["batched"][0] == 5 and len(timings["batched"][2]) == LARGE
        with capsys.disabled():
            print(f"\n[obligation register, {LARGE} obligations] batched: {timings['batched'][0]} statements, "
                  f"{timings['batched'][1] * 1000:.1f} ms; per-row: {timings['per_row'][0]} statements, "
                  f"{timings['per_row'][1] * 1000:.1f} ms")


# ------------------------------------------------------------------------------------------------ the API
@pytest.fixture(scope="module")
def orgs(client):
    """Two committed organisations with a CCO each; B also records an exception and a snapshot naming A's lineage."""
    from app.db import AdminSession
    out = {}
    with AdminSession() as db:
        w = _World(db, "api")
        for name in ("A", "B"):
            t = w.tenant()
            cco = f"olq-cco-{name.lower()}-{TAG}"
            add_principal(db, actor_id=cco, tenant_id=t, actor_type="HUMAN", display_name=cco, roles=["CCO"])
            s = w.subject(t)
            lineages = [w.obligation(t, s, w.defs[i]) for i in range(2)]
            out[name] = SimpleNamespace(tenant=t, cco=cco, lineages=lineages)
        a = out["A"].lineages[0]
        w.exception(out["A"].tenant, a, "APPROVED")
        w.snapshot(out["A"].tenant, a, AT, snapshot_id=f"{a}-snap-a", state="PROVEN")
        w.exception(out["B"].tenant, a, "APPROVED")
        w.snapshot(out["B"].tenant, a, AT + timedelta(days=9), snapshot_id=f"{a}-snap-b", state="FAILING")
        db.commit()
    return out


def test_the_api_register_is_the_callers_organisation_only(client, orgs):
    for name, other in (("A", "B"), ("B", "A")):
        r = client.get("/api/obligations", headers=H(orgs[name].cco))
        assert r.status_code == 200, r.text
        assert [o["lineage_id"] for o in r.json()] == orgs[name].lineages
        assert not {o["tenant_id"] for o in r.json()} - {orgs[name].tenant}
    a = {o["lineage_id"]: o for o in client.get("/api/obligations", headers=H(orgs["A"].cco)).json()}
    first = a[orgs["A"].lineages[0]]
    assert first["open_exceptions"] == 1                                   # B's exception on the same ID is not A's
    assert first["latest_snapshot"]["snapshot_id"] == f"{orgs['A'].lineages[0]}-snap-a"  # nor is B's newer snapshot
    assert a[orgs["A"].lineages[1]]["latest_snapshot"] is None
    # Content staff hold no tenant dossier read.
    assert client.get("/api/obligations", headers=H("actor-content-analyst")).status_code == 403
