"""D2: version-level connector certification on exact contract versions (migration 0013).

Requirements (specification text): IPE-REQ-028 (certification versioned by connector build, source API/report,
ExtractionContract and MappingContract compatibility); IPE-REQ-029 (suspension/recertification of affected contract
versions); ZA-IPE-001 §04 and §11 (immutable contract versions; source_schema_version "version or fingerprint"); IPE-REQ-002
(exact references); ZA-GLS-001 §07 (certification codes); ZA-EVT-001 connector.certification_changed, STATE_CHANGED profile;
DAT-REQ-014/024/027/037. Design and open decisions (build identity, envelope, registry relationship, run enforcement):
docs/connector-version-certification-design.md.

Fixtures create their own connectors (unique keys) so the seeded connectors that IPE tests rely on are not disturbed;
nothing can delete contract versions any more, so the rows remain in the disposable test database.
"""
from __future__ import annotations

import secrets
import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from conftest import H, add_principal, stamp

CERTIFIER, CERTIFIER_SVC, SUPER = "vc-certifier", "vc-certifier-svc", "vc-super"
FULL = {k: "PASS" for k in ("auth_scope", "tenant_isolation", "pagination_snapshot", "population_proof", "schema_mapping",
                            "transforms", "retries_idempotency", "ipe_anomalies", "version_compatibility")}


def _extraction(cid, version, **over):
    from app.models import ExtractionContractVersion
    return ExtractionContractVersion(**{
        "connector_id": cid, "version": version, "source_object": "probe_object", "endpoint_id": f"probe:/v{version}/objects",
        "query_template": "GET /objects", "record_identity": "id", "required_scopes": ["Probe.Read"], "ordering": "id ASC",
        "consistency_strategy": "SNAPSHOT_TOKEN", "pagination_method": "cursor", "population_proof_methods": ["FULL_ENUMERATION_TERMINAL"],
        "status": "UNASSESSED", **over})


def _mapping(cid, version, **over):
    from app.models import MappingContractVersion
    return MappingContractVersion(**{
        "connector_id": cid, "version": version, "source_schema_fingerprint": f"fp-v{version}-" + secrets.token_hex(4),
        "source_fields": ["id"], "field_mappings": [{"source": "id", "target": "id", "type": "string"}],
        "relied_upon_fields": ["id"], "status": "UNASSESSED", **over})


@pytest.fixture(scope="module")
def world(client):
    """Two connectors of this module (one with a passing matrix, one failing), each with two extraction and two mapping
    contract versions, plus platform principals."""
    from app.db import AdminSession
    from app.models import Actor, ConnectorDefinition
    tag = secrets.token_hex(4)
    out = {}
    with AdminSession() as db:
        for aid, atype, roles in ((CERTIFIER, "HUMAN", ["CONNECTOR_CERTIFICATION_MANAGER"]),
                                  (CERTIFIER_SVC, "SERVICE", ["CONNECTOR_CERTIFICATION_MANAGER"]),
                                  (SUPER, "HUMAN", ["SUPER_ADMIN"])):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=None, actor_type=atype, display_name=aid, roles=roles)
        for name, matrix in (("ok", FULL), ("failing", {"auth_scope": "PASS", "pagination_snapshot": "FAIL"})):
            c = ConnectorDefinition(key=f"za-vc-{name}-{tag}", name=f"VC probe {name}", provider="Probe", source_class="IDENTITY",
                                    build_version="1.0.0", release_availability="PRIVATE_BETA", certification="UNASSESSED",
                                    certification_matrix=matrix, supported_objects=["objects"])
            db.add(c)
            db.flush()
            e1, e2, m1, m2 = _extraction(c.connector_id, 1), _extraction(c.connector_id, 2), _mapping(c.connector_id, 1), \
                _mapping(c.connector_id, 2)
            db.add_all([e1, e2, m1, m2])
            db.flush()
            out[name] = {"connector": c.connector_id, "e1": e1.extraction_contract_version_id,
                         "e2": e2.extraction_contract_version_id, "m1": m1.mapping_contract_version_id,
                         "m2": m2.mapping_contract_version_id, "m1_fp": m1.source_schema_fingerprint}
        db.commit()
    return out


def _cert(client, cid, kind, vid, actor=CERTIFIER, **body):
    return client.post(f"/api/connectors/{cid}/contracts/{kind}/{vid}/certification", headers=H(actor), json=body)


def _status(model_name, vid):
    from app import models
    from app.db import AdminSession
    with AdminSession() as db:
        return db.get(getattr(models, model_name), vid).status


def _events(subject_id=None, connector_id=None):
    from app.db import AdminSession
    from app.models import ConnectorCertificationEvent as E
    with AdminSession() as db:
        q = select(E).order_by(E.record_seq)
        q = q.where(E.subject_id == subject_id) if subject_id else q.where(E.connector_id == connector_id)
        return db.execute(q).scalars().all()


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


# ------------------------------------------------------------------------------------------------ both databases
def test_each_contract_version_is_its_own_certification_subject(client, world):
    w = world["ok"]
    r = _cert(client, w["connector"], "extraction", w["e1"], certification="CERTIFIED", reason="envelope v1 tested")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CERTIFIED" and r.json()["extraction_contract_version_id"] == w["e1"]
    assert _status("ExtractionContractVersion", w["e2"]) == "UNASSESSED"   # another version of the same connector
    assert _status("MappingContractVersion", w["m1"]) == "UNASSESSED"      # same version number, other contract kind
    (ev,) = _events(w["e1"])
    assert (ev.subject_type, ev.subject_id, ev.connector_id) == ("EXTRACTION_CONTRACT_VERSION", w["e1"], w["connector"])
    assert (ev.from_certification, ev.to_certification, ev.from_runtime_health, ev.to_runtime_health) == (
        "UNASSESSED", "CERTIFIED", None, None)
    assert ev.prior_event_id is None and ev.actor_id == CERTIFIER
    assert _events(w["e2"]) == [] and _events(w["m1"]) == []


def test_suspension_and_recertification_chain_per_subject_and_leave_the_registry_alone(client, world):
    from app.models import ConnectorDefinition
    w = world["ok"]
    for target in ("CERTIFIED", "SUSPENDED", "CERTIFIED", "REVOKED"):
        assert _cert(client, w["connector"], "mapping", w["m2"], certification=target, reason=f"probe {target}").status_code == 200
    evs = _events(w["m2"])
    assert [(e.from_certification, e.to_certification) for e in evs] == [
        ("UNASSESSED", "CERTIFIED"), ("CERTIFIED", "SUSPENDED"), ("SUSPENDED", "CERTIFIED"), ("CERTIFIED", "REVOKED")]
    assert [e.prior_event_id for e in evs] == [None] + [e.event_id for e in evs[:-1]]
    (reg,) = _rows(ConnectorDefinition, connector_id=w["connector"])
    assert reg.certification == "UNASSESSED"  # the registry is independent (relationship open, D2-g)


def test_audit_and_outbox_record_the_exact_subject(client, world):
    from app.models import AuditEvent, OutboxEvent
    w = world["ok"]
    assert _cert(client, w["connector"], "mapping", w["m1"], certification="SUSPENDED", reason="schema drift").status_code == 200
    ev = _events(w["m1"])[-1]
    (a,) = [x for x in _rows(AuditEvent, object_id=w["m1"]) if x.record_seq == ev.record_seq]
    assert (a.action, a.object_type, a.tenant_id, a.prior_state, a.new_state, a.reason) == (
        "connector.certification_changed", "MappingContractVersion", None, "UNASSESSED", "SUSPENDED", "schema drift")
    (o,) = [x for x in _rows(OutboxEvent, aggregate_id=w["m1"]) if x.record_seq == ev.record_seq]
    assert (o.event_type, o.aggregate_type, o.tenant_id) == ("connector.certification_changed", "MappingContractVersion", None)
    assert o.payload == {"resource_id": w["m1"], "resource_type": "MAPPING_CONTRACT_VERSION",
                         "prior_state": {"certification": "UNASSESSED"}, "new_state": {"certification": "SUSPENDED"},
                         "revision_ref": ev.event_id, "prior_revision_ref": None,
                         "effective_scope": {"connector_id": w["connector"], "contract_version": 1,
                                             "source_schema_version": w["m1_fp"]},
                         "reason_codes": []}


def test_repeats_record_nothing(client, world):
    from test_authorization import db_fingerprint
    w = world["ok"]
    assert _cert(client, w["connector"], "extraction", w["e2"], certification="SUSPENDED", reason="first").status_code == 200
    before = db_fingerprint()
    for _ in range(2):
        r = _cert(client, w["connector"], "extraction", w["e2"], certification="SUSPENDED", reason="retry")
        assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"
    assert db_fingerprint() == before


def test_invalid_requests_and_references_change_nothing(client, world):
    from test_authorization import db_fingerprint
    ok, bad = world["ok"], world["failing"]
    before = db_fingerprint()
    assert _cert(client, bad["connector"], "mapping", bad["m1"], certification="CERTIFIED", reason="matrix FAIL").status_code == 409
    assert _cert(client, ok["connector"], "mapping", bad["m1"], certification="SUSPENDED", reason="x").status_code == 404
    assert _cert(client, ok["connector"], "mapping", "no-such-version", certification="SUSPENDED", reason="x").status_code == 404
    assert _cert(client, ok["connector"], "build", ok["m1"], certification="SUSPENDED", reason="x").status_code == 404
    assert _cert(client, ok["connector"], "mapping", ok["m1"], certification="BETA", reason="x").status_code == 422
    assert _cert(client, ok["connector"], "mapping", ok["m1"], certification="EXPIRED", reason="x").status_code == 422
    assert _cert(client, ok["connector"], "mapping", ok["m1"], certification="REVOKED", reason=" ").status_code == 422
    assert _cert(client, ok["connector"], "mapping", ok["m1"], certification="REVOKED", reason="x",
                 build="2.0").status_code == 422  # strict body
    assert db_fingerprint() == before


@pytest.mark.parametrize("actor", ["actor-admin", "actor-cco", "actor-svc", "actor-agent", "actor-ext-auditor", SUPER,
                                   CERTIFIER_SVC])
def test_only_a_human_platform_certifier_may_certify_a_version(client, world, actor):
    from test_authorization import db_fingerprint
    w = world["ok"]
    before = db_fingerprint()
    assert _cert(client, w["connector"], "extraction", w["e1"], actor=actor, certification="REVOKED",
                 reason="probe").status_code == 403
    assert db_fingerprint() == before


@pytest.mark.parametrize("fail", ["emit", "audit"])
def test_a_failure_mid_transition_leaves_no_partial_records(client, world, monkeypatch, fail):
    from test_authorization import db_fingerprint
    from app.engines import connectors
    w = world["ok"]
    before = db_fingerprint()
    monkeypatch.setattr(connectors, fail, lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"injected {fail}")))
    with pytest.raises(RuntimeError, match="injected"):
        _cert(client, w["connector"], "extraction", w["e1"], certification="REVOKED", reason="atomicity probe")
    monkeypatch.undo()
    assert db_fingerprint() == before


def test_duplicate_and_dangling_identities_are_rejected(world):
    from app.db import AdminSession
    w = world["ok"]
    for build, why in ((lambda: _extraction(w["connector"], 1), "duplicate (connector, version)"),
                       (lambda: _mapping(w["connector"], 2), "duplicate (connector, version)"),
                       (lambda: _extraction("no-such-connector", 7), "unknown connector"),
                       (lambda: _mapping(w["connector"], 9, status="BETA"), "status outside the GLS codes")):
        with AdminSession() as db:
            db.add(build())
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


def test_the_global_registry_route_is_unchanged(client, world):
    w = world["ok"]
    r = client.post(f"/api/connectors/{w['connector']}/certification", headers=H(CERTIFIER),
                    json={"certification": "SUSPENDED", "runtime_health": "DEGRADED", "reason": "registry still works"})
    assert r.status_code == 200 and (r.json()["certification"], r.json()["runtime_health"]) == ("SUSPENDED", "DEGRADED")
    ev = [e for e in _events(connector_id=w["connector"]) if e.subject_type == "CONNECTOR_DEFINITION"][-1]
    assert (ev.subject_id, ev.from_runtime_health, ev.to_runtime_health) == (None, "HEALTHY", "DEGRADED")


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


CONTRACT_TABLES = (("integration_extraction_contract_version", "extraction_contract_version_id", "e1", "endpoint_id"),
                   ("integration_mapping_contract_version", "mapping_contract_version_id", "m1", "source_schema_fingerprint"))


def test_contract_tables_privileges_triggers_and_rls(pg):
    owner, _ = pg
    with owner.connect() as c:
        for t, *_ in CONTRACT_TABLES:
            upd, dele, trunc = c.execute(text(
                "SELECT has_table_privilege('assure_app', :t, 'UPDATE'), has_table_privilege('assure_app', :t, 'DELETE'), "
                "has_table_privilege('assure_app', :t, 'TRUNCATE')"), {"t": t}).one()
            cols = set(c.execute(text(
                "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 "
                "AND NOT a.attisdropped AND has_column_privilege('assure_app', a.attrelid, a.attnum, 'UPDATE')"),
                {"t": t}).scalars())
            trig = set(c.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) AND "
                                      "NOT tgisinternal AND tgenabled = 'O'"), {"t": t}).scalars())
            forced = c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                                    "WHERE oid = CAST(:t AS regclass)"), {"t": t}).scalar()
            assert (upd, dele, trunc, cols) == (False, False, False, {"status"}), t
            assert "immutable_columns" in trig and forced, t


def test_only_a_platform_principal_changes_a_contract_and_only_its_certification(pg, world):
    owner, runtime = pg
    w = world["ok"]
    for t, pk, key, other in CONTRACT_TABLES:
        k = {"v": w[key]}
        for ctx in ({"tenant": "tenant-zoiko-group", "actor": "actor-cco"}, {"tenant": None, "actor": None},
                    {"tenant": None, "actor": CERTIFIER, "flags": ("system",)}, None):
            with runtime.connect() as c:
                tx = c.begin()
                if ctx is not None:
                    stamp(c, ctx["tenant"], actor=ctx["actor"], flags=ctx.get("flags", ()))
                assert c.execute(text(f"SELECT count(*) FROM {t} WHERE {pk} = :v"), k).scalar() == 1  # readable
                assert c.execute(text(f"UPDATE {t} SET status = 'REVOKED' WHERE {pk} = :v"), k).rowcount == 0, (t, ctx)
                tx.rollback()
        with runtime.connect() as c:
            tx = c.begin()
            stamp(c, None, actor=CERTIFIER)
            assert c.execute(text(f"UPDATE {t} SET status = 'SUSPENDED' WHERE {pk} = :v"), k).rowcount == 1
            _refused(c, f"UPDATE {t} SET {other} = 'tampered' WHERE {pk} = :v", k, "permission denied")
            _refused(c, f"UPDATE {t} SET version = version + 100 WHERE {pk} = :v", k, "permission denied")
            _refused(c, f"DELETE FROM {t} WHERE {pk} = :v", k, "permission denied")
            tx.rollback()
        with owner.connect() as c:  # the owner role: refused by the row trigger too
            tx = c.begin()
            _refused(c, f"UPDATE {t} SET {other} = 'tampered' WHERE {pk} = :v", k, "append-only except")
            _refused(c, f"DELETE FROM {t} WHERE {pk} = :v", k, "append-only")
            tx.rollback()


def test_a_tenant_cannot_insert_a_contract_version(pg, world):
    _, runtime = pg
    w = world["ok"]
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, "tenant-zoiko-group", actor="actor-cco")
        _refused(c, "INSERT INTO integration_mapping_contract_version (mapping_contract_version_id, connector_id, version, "
                    "source_schema_fingerprint, source_fields, field_mappings, relied_upon_fields, status) VALUES "
                    "('za-forged', :c, 99, 'x', '[]', '[]', '[]', 'CERTIFIED')", {"c": w["connector"]},
                 "permission denied")  # no runtime INSERT at all (R039-03), whatever the context
        tx.rollback()


def test_source_runs_must_reference_existing_contract_versions(pg):
    """IPE-REQ-002: a SourceRun's contract references are foreign keys (a dangling reference is refused)."""
    from test_immutability import _probe_row
    owner, _ = pg
    for column in ("extraction_contract_version_id", "mapping_contract_version_id"):
        with owner.connect() as c:
            tx = c.begin()
            sp = c.begin_nested()
            with pytest.raises(IntegrityError, match="fk_source_run_"):
                _probe_row(c, "evidence_source_run", **{column: "no-such-version", "tenant_id": "tenant-zoiko-group"})
            sp.rollback()
            pk, key = _probe_row(c, "evidence_source_run", tenant_id="tenant-zoiko-group")  # real references: accepted
            assert key
            tx.rollback()


def test_history_subject_and_health_are_bound(pg, world):
    owner, _ = pg
    w = world["ok"]
    with owner.connect() as c:
        tx = c.begin()
        for subject_type, subject_id, health in (("MAPPING_CONTRACT_VERSION", w["m1"], "HEALTHY"),
                                                  ("MAPPING_CONTRACT_VERSION", None, None),
                                                  ("CONNECTOR_DEFINITION", w["m1"], "HEALTHY"),
                                                  ("BUILD", w["m1"], None)):
            _refused(c, "INSERT INTO integration_connector_certification_event (event_id, connector_id, subject_type, "
                        "subject_id, build_version, from_certification, to_certification, from_runtime_health, "
                        "to_runtime_health, actor_id, reason, record_seq, created_at) VALUES (left(md5(random()::text), 32), "
                        ":c, :st, :sid, '1', 'UNASSESSED', 'CERTIFIED', :h, :h, 'probe', 'probe', 1, now())",
                     {"c": w["connector"], "st": subject_type, "sid": subject_id, "h": health}, "ck_connector_certification_event_subject")
        tx.rollback()


def test_concurrent_version_changes_serialise(pg, world):
    from app.db import SessionLocal, set_principal
    from app.engines import connectors
    from app.models import Actor
    from app.security import Ctx
    w = world["failing"]  # matrix failing: only non-CERTIFIED targets are possible
    barrier, errors = threading.Barrier(2), []

    def change(target):
        try:
            with SessionLocal() as db:
                set_principal(db, None, CERTIFIER)
                ctx = Ctx(db=db, actor=db.get(Actor, CERTIFIER))
                barrier.wait()
                connectors.set_contract_certification(ctx, w["connector"], "extraction", w["e2"], certification=target,
                                                      reason="race")
                db.commit()
        except Exception as e:  # noqa: BLE001 - asserted below
            errors.append(e)
    threads = [threading.Thread(target=change, args=(t,)) for t in ("SUSPENDED", "REVOKED")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    evs = _events(w["e2"])
    assert len(evs) == 2 and evs[1].prior_event_id == evs[0].event_id
    assert evs[1].from_certification == evs[0].to_certification
    assert _status("ExtractionContractVersion", w["e2"]) == evs[1].to_certification


def test_upgrade_preserves_existing_records_and_downgrade_refuses_to_lose_history(pg):
    from app import migrate
    owner, _ = pg
    schema = "za_vc_" + secrets.token_hex(4)
    with owner.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        migrate.upgrade("0012", schema=schema, provision_role=False)
        with owner.begin() as c:
            q = f'"{schema}".'
            c.execute(text(f"INSERT INTO {q}integration_connector_definition (connector_id, key, name, provider, source_class, "
                           "build_version, release_availability, certification, delivery_model, runtime_health, "
                           "supported_objects, certification_matrix, known_limitations) VALUES ('za-c1', 'k', 'n', 'p', 'IDENTITY', "
                           "'1.0', 'PLANNED', 'CERTIFIED', 'STANDARD', 'HEALTHY', '[]', '{}', '[]')"))
            c.execute(text(f"INSERT INTO {q}integration_mapping_contract_version (mapping_contract_version_id, connector_id, "
                           "version, source_schema_fingerprint, source_fields, field_mappings, relied_upon_fields, status) "
                           "VALUES ('za-m1', 'za-c1', 1, 'fp', '[]', '[]', '[]', 'CERTIFIED')"))
            c.execute(text(f"INSERT INTO {q}integration_connector_certification_event (event_id, connector_id, build_version, "
                           "from_certification, to_certification, from_runtime_health, to_runtime_health, actor_id, reason, "
                           "record_seq, created_at) VALUES ('za-e1', 'za-c1', '1.0', 'UNASSESSED', 'CERTIFIED', 'HEALTHY', "
                           "'HEALTHY', 'someone', 'before 0013', 1, now())"))
        migrate.upgrade(schema=schema, provision_role=False)
        with owner.connect() as c:
            q = f'"{schema}".'
            assert c.execute(text(f"SELECT status FROM {q}integration_mapping_contract_version")).scalar() == "CERTIFIED"
            rows = c.execute(text(f"SELECT event_id, subject_type, subject_id, reason FROM {q}"
                                  "integration_connector_certification_event")).all()
            assert rows == [("za-e1", "CONNECTOR_DEFINITION", None, "before 0013")]  # unchanged; nothing backfilled
            c.rollback()
        with owner.begin() as c:  # a contract-level history row (as the service writes it) makes downgrade refuse
            c.execute(text(f'INSERT INTO "{schema}".integration_connector_certification_event (event_id, connector_id, '
                           "subject_type, subject_id, build_version, from_certification, to_certification, actor_id, reason, "
                           "record_seq, created_at) VALUES ('za-e2', 'za-c1', 'MAPPING_CONTRACT_VERSION', 'za-m1', '1.0', "
                           "'CERTIFIED', 'SUSPENDED', 'someone', 'drift', 2, now())"))
        with pytest.raises(RuntimeError, match="Refusing to downgrade 0013"):
            migrate.downgrade("0012", confirm=True, schema=schema)
        assert migrate.status(schema=schema).revision == migrate.head_revision()
    finally:
        with owner.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')  # this test's throwaway schema only
