"""Stage 3c: the global connector certification registry keeps an append-only history (migration 0012).

Requirements (specification text): ZA-GLS-001 §07 and GLS-REQ-017 (certification and runtime health are separate
dimensions; certification codes UNASSESSED · CERTIFIED · SUSPENDED · REVOKED); FR-CON-002 (no CERTIFIED until the matrix
passes); ZA-DAT-001 §10 (customer surfaces derive claims from the registry); ZA-EVT-001 connector.certification_changed and
its STATE_CHANGED payload profile; EVT-REQ-001/008/025; DAT-REQ-014 (atomicity); DAT-REQ-037, SEC-REQ-029 (no rewrite or
delete of history); DAT-REQ-024 (no fabricated history); DAT-REQ-027, ZA-DAT-001 §13 (least privilege; no runtime
TRUNCATE); IAM-INV-05 (no tenant writes to platform records). Design and open decisions:
docs/connector-certification-history-design.md.

The PostgreSQL tests talk to the database directly as the runtime role (DATABASE_URL), below every application check.
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from conftest import H, add_principal, stamp

CERTIFIER, CERTIFIER_SVC, SUPER = "chm-certifier", "chm-certifier-svc", "chm-super"
ENTRA, AWS, SNOW = "entra-id", "aws-config", "servicenow-irm"   # seeded: CERTIFIED (full matrix), matrix FAIL, UNASSESSED


@pytest.fixture(scope="module")
def platform_staff(client):
    from app.db import AdminSession
    from app.models import Actor
    with AdminSession() as db:
        for aid, atype, roles in ((CERTIFIER, "HUMAN", ["CONNECTOR_CERTIFICATION_MANAGER"]),
                                  (CERTIFIER_SVC, "SERVICE", ["CONNECTOR_CERTIFICATION_MANAGER"]),
                                  (SUPER, "HUMAN", ["SUPER_ADMIN"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=None, actor_type=atype, display_name=aid, roles=roles)
        db.commit()
    return True


def _conn(key):
    from app.db import AdminSession
    from app.models import ConnectorDefinition
    with AdminSession() as db:
        return db.execute(select(ConnectorDefinition).where(ConnectorDefinition.key == key)).scalars().one()


def _events(connector_id):
    from app.db import AdminSession
    from app.models import ConnectorCertificationEvent as E
    with AdminSession() as db:
        return db.execute(select(E).where(E.connector_id == connector_id, E.subject_type == "CONNECTOR_DEFINITION")
                          .order_by(E.record_seq)).scalars().all()


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _set(client, cid, actor=CERTIFIER, **body):
    return client.post(f"/api/connectors/{cid}/certification", headers=H(actor), json=body)


@pytest.fixture
def entra(client, platform_staff):
    c = _conn(ENTRA)
    yield c
    _set(client, c.connector_id, certification=c.certification, runtime_health=c.runtime_health, reason="test restore")


# ------------------------------------------------------------------------------------------------ both databases
def test_a_certification_change_records_history_audit_and_outbox_together(client, entra):
    from app.models import AuditEvent, OutboxEvent
    cid, before = entra.connector_id, len(_events(entra.connector_id))
    r = _set(client, cid, certification="SUSPENDED", reason="Production defect affecting completeness (IPE-001)")
    assert r.status_code == 200, r.text
    assert set(r.json()) >= {"connector_id", "key", "certification", "runtime_health", "build_version"}  # unchanged shape
    assert (r.json()["certification"], r.json()["runtime_health"]) == ("SUSPENDED", entra.runtime_health)
    evs = _events(cid)
    assert len(evs) == before + 1
    ev = evs[-1]
    assert (ev.from_certification, ev.to_certification, ev.from_runtime_health, ev.to_runtime_health) == (
        entra.certification, "SUSPENDED", entra.runtime_health, entra.runtime_health)
    assert (ev.actor_id, ev.build_version, ev.reason) == (CERTIFIER, entra.build_version,
                                                         "Production defect affecting completeness (IPE-001)")
    assert ev.prior_event_id == (evs[-2].event_id if before else None)
    (a,) = [x for x in _rows(AuditEvent, object_id=cid) if x.record_seq == ev.record_seq]
    assert (a.action, a.tenant_id, a.actor_id, a.prior_state, a.new_state) == (
        "connector.certification_changed", None, CERTIFIER, f"{entra.certification}|{entra.runtime_health}",
        f"SUSPENDED|{entra.runtime_health}")  # audit contract unchanged
    (o,) = [x for x in _rows(OutboxEvent, aggregate_id=cid) if x.record_seq == ev.record_seq]
    assert (o.event_type, o.tenant_id) == ("connector.certification_changed", None)
    assert o.payload == {  # ZA-EVT-001 STATE_CHANGED profile; no free-text reason (EVT-REQ-025)
        "resource_id": cid,
        "prior_state": {"certification": entra.certification, "runtime_health": entra.runtime_health},
        "new_state": {"certification": "SUSPENDED", "runtime_health": entra.runtime_health},
        "revision_ref": ev.event_id, "prior_revision_ref": ev.prior_event_id,
        "effective_scope": {"build_version": entra.build_version}, "reason_codes": []}


def test_suspension_revocation_and_recertification_form_one_linear_history(client, entra):
    cid, start = entra.connector_id, len(_events(entra.connector_id))
    for target in ("SUSPENDED", "REVOKED", "CERTIFIED"):  # recertification passes only because the matrix is complete
        assert _set(client, cid, certification=target, reason=f"probe {target}").status_code == 200
    evs = _events(cid)[start:]
    assert [(e.from_certification, e.to_certification) for e in evs] == [
        (entra.certification, "SUSPENDED"), ("SUSPENDED", "REVOKED"), ("REVOKED", "CERTIFIED")]
    assert [e.prior_event_id for e in evs[1:]] == [e.event_id for e in evs[:-1]]  # each links to the one before


def test_a_runtime_health_change_is_history_and_audit_but_not_a_certification_event(client, entra):
    from app.models import OutboxEvent
    cid = entra.connector_id
    r = _set(client, cid, certification=entra.certification, runtime_health="DEGRADED", reason="Vendor latency incident")
    assert r.status_code == 200
    ev = _events(cid)[-1]
    assert (ev.from_certification, ev.to_certification, ev.to_runtime_health) == (
        entra.certification, entra.certification, "DEGRADED")
    assert [x for x in _rows(OutboxEvent, aggregate_id=cid) if x.record_seq == ev.record_seq] == []  # EVT-REQ-008


def test_repeated_requests_record_nothing_new(client, entra):
    """Retry / duplicate: a request whose target equals the current state changes and records nothing. Re-submitting
    CERTIFIED is not a 'renewal' (no specification defines one; design D1)."""
    from test_authorization import db_fingerprint
    cid = entra.connector_id
    before = db_fingerprint()
    for _ in range(2):
        r = _set(client, cid, certification=entra.certification, runtime_health=entra.runtime_health, reason="retry")
        assert r.status_code == 200 and r.json()["certification"] == entra.certification
        assert _set(client, cid, certification=entra.certification, reason="retry, health omitted").status_code == 200
    assert db_fingerprint() == before


def test_invalid_requests_change_nothing(client, platform_staff):
    from test_authorization import db_fingerprint
    aws, snow = _conn(AWS), _conn(SNOW)
    before = db_fingerprint()
    assert _set(client, aws.connector_id, certification="CERTIFIED", reason="matrix has a FAIL").status_code == 409
    assert _set(client, snow.connector_id, certification="CERTIFIED", reason="matrix empty").status_code == 409
    assert _set(client, snow.connector_id, certification="EXPIRED", reason="no such code").status_code == 422
    assert _set(client, snow.connector_id, certification="BETA", reason="availability, not certification").status_code == 422
    assert _set(client, snow.connector_id, certification="SUSPENDED", reason="  ").status_code == 422
    assert _set(client, snow.connector_id, certification="SUSPENDED", runtime_health="SLOW", reason="x").status_code == 422
    assert _set(client, snow.connector_id, certification="SUSPENDED", reason="x", status="ACTIVE").status_code == 422
    assert _set(client, "no-such-connector", certification="SUSPENDED", reason="x").status_code == 404
    assert db_fingerprint() == before


@pytest.mark.parametrize("actor", ["actor-admin", "actor-cco", "actor-svc", "actor-agent", "actor-ext-auditor",
                                   SUPER, CERTIFIER_SVC])
def test_only_a_human_platform_certifier_may_change_the_registry(client, platform_staff, actor):
    """Organisation principals (any role), workloads, agents, an external auditor, a Super Admin without
    certify_connectors, and a SERVICE holding the certification role are all refused, and nothing is written."""
    from test_authorization import db_fingerprint
    snow = _conn(SNOW)
    before = db_fingerprint()
    assert _set(client, snow.connector_id, actor=actor, certification="SUSPENDED", reason="probe").status_code == 403
    assert db_fingerprint() == before


@pytest.mark.parametrize("fail", ["emit", "audit"])
def test_a_failure_mid_transition_leaves_no_partial_records(client, entra, monkeypatch, fail):
    from test_authorization import db_fingerprint
    from app.engines import connectors
    before = db_fingerprint()

    def boom(*a, **k):
        raise RuntimeError(f"injected {fail} failure")
    monkeypatch.setattr(connectors, fail, boom)
    with pytest.raises(RuntimeError, match="injected"):
        _set(client, entra.connector_id, certification="SUSPENDED", reason="atomicity probe")
    monkeypatch.undo()
    assert db_fingerprint() == before
    assert _conn(ENTRA).certification == entra.certification


def test_history_is_never_fabricated():
    """Every history row was written by a person's request: each subject's chain starts once (prior NULL) and is
    linear, and no row names a migration or system actor."""
    from app.db import AdminSession
    from app.models import ConnectorCertificationEvent as E
    with AdminSession() as db:
        rows = db.execute(select(E).order_by(E.record_seq)).scalars().all()
    by: dict[str, list] = {}
    for e in rows:
        by.setdefault((e.connector_id, e.subject_type, e.subject_id), []).append(e)  # one chain per subject (0013)
    for chain in by.values():
        assert chain[0].prior_event_id is None and all(e.prior_event_id for e in chain[1:])
        assert [e.prior_event_id for e in chain[1:]] == [e.event_id for e in chain[:-1]]
    assert not any(e.actor_id.startswith("system") for e in rows)


# ------------------------------------------------------------------------------------------------ PostgreSQL below the API
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES, admin_engine, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine, engine


def _refused(conn, sql, params, match):
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError, match=match):
        conn.execute(text(sql), params)
    sp.rollback()


def test_runtime_privileges_triggers_and_rls(pg):
    owner, _ = pg
    with owner.connect() as c:
        for t, cols, trig in (("integration_connector_definition", {"certification", "runtime_health"}, "immutable_columns"),
                              ("integration_connector_certification_event", set(), "append_only")):
            upd, dele, trunc, sel, ins = c.execute(text(
                "SELECT has_table_privilege('assure_app', :t, 'UPDATE'), has_table_privilege('assure_app', :t, 'DELETE'), "
                "has_table_privilege('assure_app', :t, 'TRUNCATE'), has_table_privilege('assure_app', :t, 'SELECT'), "
                "has_table_privilege('assure_app', :t, 'INSERT')"), {"t": t}).one()
            granted = set(c.execute(text(
                "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 "
                "AND NOT a.attisdropped AND has_column_privilege('assure_app', a.attrelid, a.attnum, 'UPDATE')"),
                {"t": t}).scalars())
            triggers = set(c.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) "
                                          "AND NOT tgisinternal AND tgenabled = 'O'"), {"t": t}).scalars())
            forced = c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                                    "WHERE oid = CAST(:t AS regclass)"), {"t": t}).scalar()
            # the registry: no runtime INSERT (no runtime writer creates it; R039-03); the history: INSERT for its writer
            assert (upd, dele, trunc, sel, ins, granted) == (
                False, False, False, True, t != "integration_connector_definition", cols), t
            assert trig in triggers and forced, t


def test_no_tenant_context_can_write_the_registry_or_its_history(pg, platform_staff):
    _, runtime = pg
    cid = _conn(SNOW).connector_id
    k = {"c": cid}
    insert_hist = ("INSERT INTO integration_connector_certification_event (event_id, connector_id, build_version, "
                   "from_certification, to_certification, from_runtime_health, to_runtime_health, actor_id, reason, "
                   "record_seq, created_at) VALUES (left(md5(random()::text), 32), :c, '0', 'UNASSESSED', "
                   "'CERTIFIED', 'HEALTHY', 'HEALTHY', 'forger', 'forged', 1, now())")
    for ctx in ({"tenant": "tenant-zoiko-group"}, {"tenant": None}, {"tenant": None, "flags": ("system",)}, None):
        with runtime.connect() as c:
            tx = c.begin()
            if ctx is not None:
                stamp(c, ctx["tenant"], actor="actor-cco" if ctx["tenant"] else None, flags=ctx.get("flags", ()))
            # the registry stays readable (customer surfaces derive claims from it) ...
            assert c.execute(text("SELECT count(*) FROM integration_connector_definition")).scalar() >= 4
            # ... but cannot be changed, and its history can be neither forged nor read
            assert c.execute(text("UPDATE integration_connector_definition SET certification = 'CERTIFIED' "
                                  "WHERE connector_id = :c"), k).rowcount == 0, ctx
            _refused(c, insert_hist, k, "row-level security")
            assert c.execute(text("SELECT count(*) FROM integration_connector_certification_event")).scalar() == 0
            tx.rollback()
    with runtime.connect() as c:  # system scope with an actor is still not a platform principal
        tx = c.begin()
        stamp(c, None, actor=CERTIFIER, flags=("system",))
        assert c.execute(text("UPDATE integration_connector_definition SET certification = 'CERTIFIED' "
                              "WHERE connector_id = :c"), k).rowcount == 0
        tx.rollback()


def test_a_platform_principal_changes_only_the_two_dimensions_and_never_deletes(pg, platform_staff):
    owner, runtime = pg
    cid = _conn(SNOW).connector_id
    k = {"c": cid}
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, None, actor=CERTIFIER)
        assert c.execute(text("UPDATE integration_connector_definition SET certification = 'SUSPENDED', "
                              "runtime_health = 'FAILED' WHERE connector_id = :c"), k).rowcount == 1
        for col in ("build_version = 'tampered'", "release_availability = 'GENERAL_AVAILABILITY'",
                    "certification_matrix = CAST('{}' AS json)", "key = 'renamed'"):
            _refused(c, f"UPDATE integration_connector_definition SET {col} WHERE connector_id = :c", k, "permission denied")
        _refused(c, "DELETE FROM integration_connector_definition WHERE connector_id = :c", k, "permission denied")
        _refused(c, "UPDATE integration_connector_certification_event SET reason = 'rewritten'", {}, "permission denied")
        _refused(c, "DELETE FROM integration_connector_certification_event", {}, "permission denied")
        tx.rollback()
    with owner.connect() as c:  # the owner role: refused by the row triggers too
        tx = c.begin()
        _refused(c, "UPDATE integration_connector_definition SET build_version = 'x' WHERE connector_id = :c", k,
                 "append-only except")
        _refused(c, "DELETE FROM integration_connector_definition WHERE connector_id = :c", k, "append-only")
        tx.rollback()


def test_history_rows_cannot_be_rewritten_by_any_role(client, entra, pg):
    owner, _ = pg
    assert _set(client, entra.connector_id, certification="SUSPENDED", reason="history probe").status_code == 200
    eid = _events(entra.connector_id)[-1].event_id
    with owner.connect() as c:
        tx = c.begin()
        _refused(c, "UPDATE integration_connector_certification_event SET reason = 'x' WHERE event_id = :e", {"e": eid},
                 "append-only")
        _refused(c, "DELETE FROM integration_connector_certification_event WHERE event_id = :e", {"e": eid}, "append-only")
        tx.rollback()


def test_concurrent_changes_serialise_into_a_linear_history(pg, entra, platform_staff):
    """Two certifiers change the same connector at once: the row lock orders them; each event is recorded against the
    state it actually replaced, and the second links to the first (no fork, no lost update)."""
    from app.db import SessionLocal, set_principal
    from app.engines import connectors
    from app.models import Actor
    from app.security import Ctx
    cid, start = entra.connector_id, len(_events(entra.connector_id))
    barrier, errors = threading.Barrier(2), []

    def change(cert, health):
        try:
            with SessionLocal() as db:
                set_principal(db, None, CERTIFIER)
                ctx = Ctx(db=db, actor=db.get(Actor, CERTIFIER))
                barrier.wait()
                connectors.set_certification(ctx, cid, certification=cert, runtime_health=health, reason="race")
                db.commit()
        except Exception as e:  # noqa: BLE001 - asserted below
            errors.append(e)
    threads = [threading.Thread(target=change, args=a) for a in (("SUSPENDED", None), ("SUSPENDED", "DEGRADED"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    evs = _events(cid)[start:]
    assert 1 <= len(evs) <= 2
    assert evs[0].from_certification == entra.certification
    for a, b in zip(evs, evs[1:]):
        assert b.prior_event_id == a.event_id and (b.from_certification, b.from_runtime_health) == (
            a.to_certification, a.to_runtime_health)
    final = _conn(ENTRA)
    assert (final.certification, final.runtime_health) == (evs[-1].to_certification, evs[-1].to_runtime_health)


def test_the_migration_backfills_nothing(pg):
    """An existing registry row gets no invented history from 0012 (DAT-REQ-024)."""
    import secrets
    from app import migrate
    owner, _ = pg
    schema = "za_cert_" + secrets.token_hex(4)
    with owner.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        migrate.upgrade("0011", schema=schema, provision_role=False)
        with owner.begin() as c:
            c.execute(text(f'INSERT INTO "{schema}".integration_connector_definition (connector_id, key, name, provider, '
                           "source_class, build_version, release_availability, certification, delivery_model, "
                           "runtime_health, supported_objects, certification_matrix, known_limitations) VALUES "
                           "('za-c1', 'za-key', 'n', 'p', 'IDENTITY', '1.0', 'PLANNED', 'CERTIFIED', 'STANDARD', "
                           "'HEALTHY', '[]', '{}', '[]')"))
        migrate.upgrade(schema=schema, provision_role=False)
        with owner.connect() as c:
            assert c.execute(text(f'SELECT count(*) FROM "{schema}".integration_connector_certification_event')).scalar() == 0
            assert c.execute(text(f'SELECT certification FROM "{schema}".integration_connector_definition')).scalar() == "CERTIFIED"
            c.rollback()
    finally:
        with owner.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')  # this test's throwaway schema only
