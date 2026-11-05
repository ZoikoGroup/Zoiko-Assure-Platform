"""Task 2: database-enforced tenant context, platform-row isolation, DAT §13 roles and runtime-role limits (PostgreSQL).

Like tests/test_rls.py these talk to PostgreSQL directly as the runtime role (DATABASE_URL), below every application
check. ZA-DAT-001 §12 (transaction-local, verified context; pooling; security definer), §13 (roles, ownership,
prohibitions), §16 (outbox); DAT-REQ-003/004/005/027/028; TEN-REQ-022; IAM-REQ-014/015/041; IAM §22; SEC-REQ-008/023/045.

Finding I-01 (fixed by migration 0007): the policies used to trust app.tenant_id / app.system, which any session can
set. They now trust only a context the database verifies (HMAC under a key the runtime role cannot read, bound to one
transaction), so these tests attack the database with every way the runtime role can set, forge, replay or switch a
context, in separate statements and inside one statement, and expect nothing. What they cannot show: protection
against a process that holds the context key (the API itself); see docs/task-2-authentication-session-and-tenant-security-report.md.
"""
import hashlib
import hmac
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from conftest import stamp, add_principal

TENANT, OTHER = "tenant-zoiko-group", "tenant-t2-pg-other"
PLATFORM_ACTOR = "t2-pg-platform-probe"
PLATFORM_LOGS = ("platform_audit_event", "platform_outbox_event")
IDENTITY = ("auth_session", "auth_credential")
ROLE_REGISTRY = ("iam_role_assignment", "iam_role_assignment_event")  # migration 0009
# Global tables with no tenant (migration 0012): every runtime context reads the registry; only a verified platform
# principal writes it or reads and writes its history.
GLOBAL_REGISTRY = ("integration_connector_definition", "integration_connector_certification_event",
                   "integration_extraction_contract_version", "integration_mapping_contract_version")  # + 0013
PG_USER, PG_EMAIL = "t2-pg-user", "t2.pg.user@test.example"
DAT13_ROLES = ("assure_table_owner", "assure_migrator", "assure_app_rw", "assure_app_ro", "assure_outbox_publisher",
               "assure_catalog_loader", "assure_analytics_ro", "assure_breakglass_admin")

AUDIT_INSERT = text("INSERT INTO platform_audit_event (id, tenant_id, action, object_type, object_id, record_seq, recorded_at) "
                    "VALUES (:id, :t, 't2.rls_probe', 'Probe', :id, 0, now())")
OUTBOX_INSERT = text("INSERT INTO platform_outbox_event (event_id, tenant_id, cell_id, aggregate_type, aggregate_id, "
                     "event_type, schema_version, dictionary_version, record_seq, payload, available_at, attempts) "
                     "VALUES (:id, :t, 'probe', 'Probe', :id, 'security.content_signature_failed', 1, 'probe', 0, "
                     "'{}', now(), 0)")
ROW_INSERT = text("INSERT INTO org_subject_registry (subject_id, tenant_id, subject_type, display_name, active) "
                  "VALUES (:id, :t, 'BUSINESS_UNIT', 't2 probe', true)")


@pytest.fixture(scope="module")
def pg(client):  # client fixture: this session's migration run, provisioning and seed have happened
    from app.db import IS_POSTGRES, CELL_ID, AdminSession, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.models import Tenant
    with AdminSession() as db:  # a second organisation with one row in a mutable tenant table
        if db.get(Tenant, OTHER) is None:
            db.add(Tenant(tenant_id=OTHER, legal_name="T2 PG Other", contracting_region=CELL_ID))
            db.commit()
    return engine


def _id() -> str:
    from app.models import new_id
    return new_id()


def _count(conn, table: str, where: str = "true") -> int:
    return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar()


def _tenant_tables(conn) -> list[str]:
    return conn.execute(text(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
        "AND c.relkind = 'r' AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' "
        "AND NOT a.attisdropped) ORDER BY 1")).scalars().all()


def _visible_tenants(conn) -> set:
    return set(conn.execute(text("SELECT tenant_id FROM org_tenant")).scalars())


def _forged(tenant, *, flags=(), nonce=None, conn=None) -> str:
    """A well-formed context signed with a key that is not the database's."""
    nonce = nonce or conn.execute(text("SELECT assure_context_nonce()")).scalar_one()
    body = "|".join(["v1", tenant or "", "attacker", "", ",".join(flags), nonce])
    return body + "|" + hmac.new(secrets.token_bytes(32), body.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------------------------------------ I-01: forged context
def test_unsigned_settings_grant_nothing(pg):
    """The 0002 mechanism: app.tenant_id / app.system set by the runtime role itself. Ignored since 0007."""
    with pg.connect() as c:
        tx = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.system', 'on', true)"), {"t": TENANT})
        c.exec_driver_sql("SET LOCAL app.tenant_id = 'tenant-zoiko-group'")
        assert _visible_tenants(c) == set()
        for t in ("evidence_artifact", "org_actor", *PLATFORM_LOGS, *IDENTITY):
            assert _count(c, t) == 0, t
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(ROW_INSERT, {"id": _id(), "t": TENANT})
        tx.rollback()


def test_forged_tampered_and_replayed_contexts_are_refused(pg):
    with pg.connect() as c:
        # a context signed with another key, claiming the tenant and system scope
        tx = c.begin()
        c.execute(text("SELECT set_config('app.context', :c, true)"),
                  {"c": _forged(TENANT, flags=("system", "auth"), conn=c)})
        assert _visible_tenants(c) == set() and _count(c, "auth_session") == 0
        tx.rollback()
        # a genuine context with its tenant swapped (the mac no longer matches)
        tx = c.begin()
        stamp(c, OTHER)
        genuine = c.execute(text("SELECT current_setting('app.context')")).scalar()
        assert _visible_tenants(c) == {OTHER}
        c.execute(text("SELECT set_config('app.context', :c, true)"), {"c": genuine.replace(f"|{OTHER}|", f"|{TENANT}|", 1)})
        assert _visible_tenants(c) == set()
        tx.commit()
        # the genuine context replayed in the next transaction on the same connection, and on another connection
        tx = c.begin()
        c.execute(text("SELECT set_config('app.context', :c, true)"), {"c": genuine})
        assert _visible_tenants(c) == set()
        tx.rollback()
    with pg.connect() as other:
        tx = other.begin()
        other.execute(text("SELECT set_config('app.context', :c, true)"), {"c": genuine})
        assert _visible_tenants(other) == set()
        tx.rollback()


def test_context_cannot_be_switched_inside_one_statement(pg):
    """The earlier probe widened one SELECT with set_config in a sub-select; now nothing the statement sets is trusted."""
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, OTHER)
        for attack, params in (("set_config('app.system', 'on', true)", {}),
                               ("set_config('app.tenant_id', 'tenant-zoiko-group', true)", {}),
                               ("set_config('app.context', :forged, true)",
                                {"forged": _forged(TENANT, flags=("system",), conn=c)})):
            row = c.execute(text(f"SELECT (SELECT {attack}) AS s, (SELECT count(*) FROM org_tenant "
                                 "WHERE tenant_id <> 'tenant-t2-pg-other') AS leaked"), params).one()
            assert row.leaked == 0, attack
            stamp(c, OTHER)  # restore the genuine context for the next attack
        tx.rollback()


def test_the_context_key_is_out_of_reach_and_the_verifier_is_narrow(pg):
    with pg.connect() as c:
        tx = c.begin()
        with pytest.raises(DBAPIError) as refused:
            c.execute(text("SELECT * FROM assure_context_key"))
        assert refused.value.orig.sqlstate == "42501"
        tx.rollback()
        assert c.execute(text("SELECT assure_context_key_id()")).scalar()  # a non-secret identifier only
        definers = c.execute(text(
            "SELECT p.proname, pg_get_userbyid(p.proowner), r.rolcanlogin, p.proconfig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_roles r ON r.oid = p.proowner "
            "WHERE n.nspname = 'public' AND p.prosecdef ORDER BY 1")).all()
    # DAT §12 / DAT-REQ-027 / SEC-REQ-045: two definers, non-login owner, fixed search_path
    assert [(n, o, login, cfg) for n, o, login, cfg in definers] == [
        ("assure_context", "assure_table_owner", False, ["search_path=pg_catalog"]),
        ("assure_context_key_id", "assure_table_owner", False, ["search_path=pg_catalog"])]


# ------------------------------------------------------------------------------------------------ fail closed / cross-tenant
def test_missing_context_fails_closed_on_every_tenant_table(pg):
    with pg.connect() as c:
        tx = c.begin()
        for t in _tenant_tables(c):
            assert _count(c, t) == 0, t
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(ROW_INSERT, {"id": _id(), "t": TENANT})
        tx.rollback()


def test_cross_tenant_select_insert_update_delete_are_denied(pg):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        c.execute(ROW_INSERT, {"id": (own := _id()), "t": TENANT})  # the victim's row, written in its own scope
        stamp(c, OTHER)
        assert _count(c, "org_subject_registry", f"subject_id = '{own}'") == 0
        assert c.execute(text("UPDATE org_subject_registry SET display_name = 'stolen' WHERE subject_id = :i"),
                         {"i": own}).rowcount == 0
        assert c.execute(text("DELETE FROM org_subject_registry WHERE subject_id = :i"), {"i": own}).rowcount == 0
        sp = c.begin_nested()
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(ROW_INSERT, {"id": _id(), "t": TENANT})
        sp.rollback()
        c.execute(ROW_INSERT, {"id": (mine := _id()), "t": OTHER})  # its own tenant: allowed
        with pytest.raises(ProgrammingError, match="row-level security"):  # and cannot be moved to another tenant
            c.execute(text("UPDATE org_subject_registry SET tenant_id = :t WHERE subject_id = :i"), {"t": TENANT, "i": mine})
        tx.rollback()


def test_the_owner_itself_is_subject_to_rls_and_only_the_migrator_path_is_privileged(pg):
    """FORCE ROW LEVEL SECURITY: the non-login owner sees no rows; the explicit privileged path is assure_migrator."""
    from app.db import admin_engine
    with admin_engine.connect() as c:
        tx = c.begin()
        assert _count(c, "evidence_artifact") > 0  # the migration login, through migrator_all
        c.exec_driver_sql("SET LOCAL ROLE assure_table_owner")
        assert _count(c, "evidence_artifact") == 0
        tx.rollback()


# ------------------------------------------------------------------------------------------------ platform rows (0005/0007)
def test_a_tenant_scope_cannot_see_update_or_delete_platform_rows(pg):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, None, actor=PLATFORM_ACTOR)  # platform scope writes its own no-tenant records
        c.execute(AUDIT_INSERT, {"id": _id(), "t": None})
        c.execute(OUTBOX_INSERT, {"id": _id(), "t": None})
        assert all(_count(c, t, "tenant_id IS NULL") > 0 for t in PLATFORM_LOGS)
        stamp(c, TENANT)
        for t in PLATFORM_LOGS:
            assert _count(c, t, "tenant_id IS NULL") == 0, t
        assert c.execute(text("UPDATE platform_outbox_event SET attempts = attempts + 1 WHERE tenant_id IS NULL")).rowcount == 0
        assert c.execute(text("DELETE FROM platform_outbox_event WHERE tenant_id IS NULL")).rowcount == 0
        tx.rollback()


@pytest.mark.parametrize("insert", [AUDIT_INSERT, OUTBOX_INSERT], ids=PLATFORM_LOGS)
def test_a_tenant_scope_cannot_write_a_platform_row(pg, insert):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(insert, {"id": _id(), "t": None})
        tx.rollback()


@pytest.mark.parametrize("insert", [AUDIT_INSERT, OUTBOX_INSERT], ids=PLATFORM_LOGS)
def test_the_platform_scope_cannot_write_or_see_tenant_rows(pg, insert):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, None, actor=PLATFORM_ACTOR)
        assert _count(c, insert.text.split()[2], "tenant_id IS NOT NULL") == 0
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(insert, {"id": _id(), "t": TENANT})
        tx.rollback()


def test_platform_rows_need_a_verified_platform_principal(pg):
    """No context, or an anonymous one (sign-in), is not a platform reader; sign-in may still record its failures."""
    with pg.connect() as c:
        tx = c.begin()
        assert all(_count(c, t, "tenant_id IS NULL") == 0 for t in PLATFORM_LOGS)
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(AUDIT_INSERT, {"id": _id(), "t": None})
        tx.rollback()
        tx = c.begin()
        stamp(c, None, flags=("auth",))  # pre-authentication: no actor yet
        c.execute(AUDIT_INSERT, {"id": _id(), "t": None})
        assert all(_count(c, t, "tenant_id IS NULL") == 0 for t in PLATFORM_LOGS)
        tx.rollback()


def test_the_platform_scope_cannot_rewrite_platform_rows(pg):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, None, actor=PLATFORM_ACTOR)
        c.execute(OUTBOX_INSERT, {"id": (eid := _id()), "t": None})
        assert c.execute(text("UPDATE platform_outbox_event SET attempts = 9 WHERE event_id = :e"), {"e": eid}).rowcount == 0
        assert c.execute(text("DELETE FROM platform_outbox_event WHERE event_id = :e"), {"e": eid}).rowcount == 0
        tx.rollback()


def test_a_tenant_scope_sees_only_its_own_platform_log_rows(pg):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        for t in PLATFORM_LOGS:
            assert _count(c, t, "tenant_id = 'tenant-zoiko-group'") > 0, t
            assert _count(c, t, "tenant_id IS DISTINCT FROM 'tenant-zoiko-group'") == 0, t
        tx.rollback()


# ------------------------------------------------------------------------------------------------ identity tables
def test_identity_tables_need_the_signed_auth_capability(pg):
    """ZA-IAM-001 §22: sign-in identity discovery and the session/credential tables are a separately controlled path."""
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, TENANT, actor="actor-cco")
        assert _count(c, "auth_credential") == 0 and _count(c, "auth_session") == 0
        assert _count(c, "org_actor", "tenant_id IS DISTINCT FROM 'tenant-zoiko-group'") == 0
        sp = c.begin_nested()
        with pytest.raises(ProgrammingError, match="row-level security"):  # no session minting
            c.execute(text("INSERT INTO auth_session (session_id, actor_id, created_at, expires_at, token_hash) "
                           "VALUES (:i, 'actor-cco', now(), now() + interval '1 hour', :h)"),
                      {"i": _id(), "h": secrets.token_hex(32)})
        sp.rollback()
        stamp(c, None, flags=("auth",))
        assert _count(c, "auth_credential") > 0 and _count(c, "org_actor", "tenant_id IS DISTINCT FROM 'tenant-zoiko-group'") > 0
        tx.rollback()


# ------------------------------------------------------------------------------------------------ RLS inventory
def test_every_tenant_and_identity_table_has_forced_rls_and_reviewed_policies(pg):
    with pg.connect() as c:
        flags = dict((r[0], r[1:]) for r in c.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'")).all())
        tenant_tables = set(_tenant_tables(c))
        policies = c.execute(text("SELECT tablename, policyname, cmd, roles::text, coalesce(qual, ''), "
                                  "coalesce(with_check, '') FROM pg_policies WHERE schemaname = 'public'")).all()
    # 42 + 6 Stage 3c governance event tables + 2 Stage 4b role registry tables + the 0011 authority grant event table
    assert len(tenant_tables) == 51
    for t in tenant_tables | set(IDENTITY) | set(GLOBAL_REGISTRY):
        assert flags[t] == (True, True), t  # RLS enabled and forced (DAT-REQ-003, TEN-REQ-022)
    by_table: dict[str, set] = {}
    for table, name, cmd, roles, qual, check in policies:
        by_table.setdefault(table, set()).add((name, cmd))
        if name == "tenant_isolation":
            assert roles == "{assure_app_ro,assure_app_rw}", (table, roles)
            assert "IS NULL" not in qual + check and "assure_current_tenant()" in qual, table
        if name == "migrator_all":
            assert roles == "{assure_migrator}", table
        assert "app.tenant_id" not in qual + check and "current_setting" not in qual + check, table
    assert set(by_table) == tenant_tables | set(IDENTITY) | set(GLOBAL_REGISTRY)
    assert by_table["integration_connector_definition"] == {("registry_read", "SELECT"), ("registry_platform_update", "UPDATE"),
                                                         ("migrator_all", "ALL")}  # no runtime INSERT (R039-03)
    assert by_table["integration_connector_certification_event"] == {("registry_history_read", "SELECT"),
                                                                  ("registry_history_insert", "INSERT"), ("migrator_all", "ALL")}
    for t in ("integration_extraction_contract_version", "integration_mapping_contract_version"):  # migration 0013
        assert by_table[t] == {("contract_read", "SELECT"), ("contract_platform_update", "UPDATE"),
                               ("migrator_all", "ALL")}, t  # no runtime INSERT (R039-03)
    for t in tenant_tables - set(PLATFORM_LOGS) - set(ROLE_REGISTRY) - {"org_actor"}:
        assert by_table[t] == {("tenant_isolation", "ALL"), ("migrator_all", "ALL")}, t
    for t in ROLE_REGISTRY:  # Zoiko platform principals' assignments: readable by a verified platform principal only
        assert by_table[t] == {("tenant_isolation", "ALL"), ("migrator_all", "ALL"),
                               ("platform_rows_select", "SELECT")}, t
    for t in PLATFORM_LOGS:
        assert {("tenant_isolation", "ALL"), ("migrator_all", "ALL"), ("platform_rows_select", "SELECT"),
                ("platform_rows_insert", "INSERT")} <= by_table[t], t
    assert by_table["org_actor"] == {("tenant_isolation", "ALL"), ("migrator_all", "ALL"),
                                     ("platform_actors", "SELECT"), ("identity_lookup", "SELECT")}
    assert by_table["auth_session"] == {("auth_access", "ALL"), ("migrator_all", "ALL"), ("session_self", "SELECT"),
                                        ("session_self_lock", "UPDATE")}


# ------------------------------------------------------------------------------------------------ tenant context lifecycle
def test_tenant_context_does_not_survive_the_transaction_on_a_reused_connection(pg):
    """DAT-REQ-005 / DAT §12 "Connection pooling": transaction-local, whether the transaction commits or rolls back."""
    from app.db import SessionLocal, set_principal
    with pg.connect() as conn:
        pid = conn.execute(text("SELECT pg_backend_pid()")).scalar()
        conn.commit()
        for end in ("commit", "rollback"):
            db = SessionLocal(bind=conn)
            set_principal(db, TENANT, "actor-cco")
            assert db.execute(text("SELECT assure_current_tenant()")).scalar() == TENANT
            assert db.execute(text("SELECT count(*) FROM evidence_artifact")).scalar() > 0
            getattr(db, end)()
            db.close()
            assert conn.execute(text("SELECT pg_backend_pid(), coalesce(current_setting('app.context', true), ''), "
                                     "assure_current_tenant()")).one() == (pid, "", None), end
            assert conn.execute(text("SELECT count(*) FROM evidence_artifact")).scalar() == 0, end
            conn.rollback()


def test_nothing_set_on_a_connection_survives_its_return_to_the_pool(pg):
    """DAT §12 pooling, beyond the transaction-local stamp: session-level settings and a temporary table shadowing a
    real one are cleared at check-in (app/db.py)."""
    raw = pg.raw_connection()
    dbapi = raw.dbapi_connection
    with dbapi.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false), set_config('app.system', 'on', false), "
                    "set_config('app.context', 'v1|planted', false)", (TENANT,))
        cur.execute("CREATE TEMP TABLE platform_audit_event (id text)")
    dbapi.commit()
    raw.close()  # checked in: the pool's reset runs here
    try:
        with dbapi.cursor() as cur:  # the very connection the next checkout is handed
            cur.execute("SELECT coalesce(current_setting('app.tenant_id', true), ''), "
                        "coalesce(current_setting('app.system', true), ''), coalesce(current_setting('app.context', true), ''), "
                        "(SELECT count(*) FROM pg_class WHERE relname = 'platform_audit_event' AND relpersistence = 't')")
            assert cur.fetchone() == ("", "", "", 0)
    finally:
        dbapi.rollback()


def test_concurrent_requests_on_pooled_connections_never_see_each_others_tenant(pg):
    from app.db import SessionLocal, set_principal

    def request(i):
        tenant = (TENANT, OTHER)[i % 2]
        db = SessionLocal()
        try:
            set_principal(db, tenant, f"probe-{i}")
            seen = _visible_tenants(db)
            ctx = db.execute(text("SELECT assure_current_tenant()")).scalar()
            db.commit()
            return tenant, seen, ctx
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(200)))
    assert all(seen == {tenant} and ctx == tenant for tenant, seen, ctx in results)


def test_a_rolled_back_savepoint_restores_the_outer_context(pg):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        sp = c.begin_nested()
        stamp(c, OTHER)
        assert c.execute(text("SELECT assure_current_tenant()")).scalar() == OTHER
        sp.rollback()
        assert c.execute(text("SELECT assure_current_tenant()")).scalar() == TENANT
        tx.rollback()


def test_row_security_off_is_refused_not_a_bypass(pg):
    with pg.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        c.exec_driver_sql("SET LOCAL row_security = off")
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(text("SELECT count(*) FROM evidence_artifact"))
        tx.rollback()


# ------------------------------------------------------------------------------------------------ DAT §13 roles
ESCALATIONS = {
    "set_role_to_migration_login": 'SET ROLE "{owner}"',
    "set_role_to_table_owner": "SET ROLE assure_table_owner",
    "set_role_to_migrator": "SET ROLE assure_migrator",
    "set_role_to_app_rw": "SET ROLE assure_app_rw",
    "set_role_to_breakglass": "SET ROLE assure_breakglass_admin",
    "set_session_authorization": 'SET SESSION AUTHORIZATION "{owner}"',
    "grant_owner_role_to_self": "GRANT assure_table_owner TO CURRENT_USER",
    "grant_app_rw_with_set": "GRANT assure_app_rw TO CURRENT_USER WITH SET TRUE",
    "give_self_bypassrls": "ALTER ROLE CURRENT_USER BYPASSRLS",
    "create_role": "CREATE ROLE za_t2_probe_role",
    "disable_rls": "ALTER TABLE evidence_artifact DISABLE ROW LEVEL SECURITY",
    "change_force_rls": "ALTER TABLE evidence_artifact NO FORCE ROW LEVEL SECURITY",
    "drop_policy": "DROP POLICY tenant_isolation ON platform_outbox_event",
    "alter_policy": "ALTER POLICY tenant_isolation ON evidence_artifact USING (true)",
    "replace_context_function": "CREATE OR REPLACE FUNCTION assure_current_tenant() RETURNS text LANGUAGE sql AS $$ SELECT 'x' $$",
    "replace_verifier": "CREATE OR REPLACE FUNCTION assure_system() RETURNS boolean LANGUAGE sql AS $$ SELECT true $$",
    "write_context_key": "UPDATE assure_context_key SET key_id = 'x'",
    "create_table": "CREATE TABLE za_t2_probe (id int)",
    "create_schema": "CREATE SCHEMA za_t2_probe",
    "truncate_tenant_table": "TRUNCATE evidence_artifact",
    "disable_append_only_trigger": "ALTER TABLE evidence_ledger_event DISABLE TRIGGER append_only",
    "take_table_ownership": "ALTER TABLE evidence_artifact OWNER TO CURRENT_USER",
    "write_migration_state": "UPDATE alembic_version SET version_num = 'x'",
}


@pytest.mark.parametrize("name", sorted(ESCALATIONS))
def test_runtime_role_cannot_escalate_or_disable_enforcement(pg, name):
    """DAT §13 prohibitions, DAT-REQ-004/027, SEC-REQ-023: refused with insufficient_privilege (42501)."""
    from sqlalchemy.engine import make_url
    from app.db import ADMIN_DATABASE_URL
    sql = ESCALATIONS[name].format(owner=make_url(ADMIN_DATABASE_URL).username)
    with pg.connect() as c:
        tx = c.begin()
        with pytest.raises(DBAPIError) as refused:
            c.exec_driver_sql(sql)
        tx.rollback()
    assert getattr(refused.value.orig, "sqlstate", None) == "42501", (name, str(refused.value.orig))


def test_dat13_roles_memberships_and_ownership(pg):
    from sqlalchemy.engine import make_url
    from app.db import ADMIN_DATABASE_URL
    login = make_url(ADMIN_DATABASE_URL).username
    with pg.connect() as c:
        attrs = dict((r[0], r[1:]) for r in c.execute(text(
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
            "FROM pg_roles WHERE rolname = ANY(:r)"), {"r": list(DAT13_ROLES)}).all())
        members = set(c.execute(text(
            "SELECT g.rolname, m.rolname, a.inherit_option, a.set_option FROM pg_auth_members a "
            "JOIN pg_roles g ON g.oid = a.roleid JOIN pg_roles m ON m.oid = a.member "
            "WHERE g.rolname = ANY(:r) AND NOT a.admin_option"), {"r": list(DAT13_ROLES)}).all())
        owners = set(c.execute(text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind IN ('r', 'p', 'v', 'm', 'S') UNION SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE pronamespace = 'public'::regnamespace UNION SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
            "WHERE nspname = 'public'")).scalars())
        grants = set(c.execute(text(
            "SELECT r.rolname, c.relname, a.privilege_type FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a "
            "JOIN pg_roles r ON r.oid = a.grantee WHERE c.relnamespace = 'public'::regnamespace "
            "AND r.rolname IN ('assure_outbox_publisher', 'assure_analytics_ro', 'assure_breakglass_admin')")).all())
        ro_on_identity = c.execute(text(
            "SELECT count(*) FROM unnest(ARRAY['auth_session', 'auth_credential', 'assure_context_key']) t "
            "WHERE has_table_privilege('assure_app_ro', t, 'SELECT')")).scalar()
    assert set(attrs) == set(DAT13_ROLES) and all(v == (False,) * 6 for v in attrs.values())  # NOLOGIN, no attributes
    assert ("assure_table_owner", "assure_migrator", True, True) in members
    assert ("assure_migrator", login, True, True) in members
    assert ("assure_app_rw", "assure_app", True, False) in members
    assert not {m for m in members if m[0] in ("assure_app_ro", "assure_outbox_publisher", "assure_catalog_loader",
                                                 "assure_analytics_ro", "assure_breakglass_admin")}
    assert owners == {"assure_table_owner"}  # non-login owner of every object and the schema; runtime owns nothing
    assert grants == {("assure_outbox_publisher", "platform_outbox_event", "SELECT")}  # + delivery-column UPDATE only
    assert ro_on_identity == 0


def test_runtime_role_holds_no_membership_ownership_or_extra_table_privilege(pg):
    with pg.connect() as c:
        role = c.execute(text("SELECT current_user")).scalar()
        assert c.execute(text("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
                              "FROM pg_roles WHERE rolname = current_user")).one() == (False,) * 5
        assert c.execute(text("SELECT g.rolname, a.admin_option, a.set_option FROM pg_auth_members a JOIN pg_roles g "
                              "ON g.oid = a.roleid WHERE a.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
                              )).all() == [("assure_app_rw", False, False)]
        for catalog, owner in (("pg_class", "relowner"), ("pg_proc", "proowner"), ("pg_namespace", "nspowner"),
                               ("pg_type", "typowner")):
            assert c.execute(text(f"SELECT count(*) FROM {catalog} WHERE {owner} = "
                                  "(SELECT oid FROM pg_roles WHERE rolname = current_user)")).scalar() == 0, catalog
        assert not c.execute(text("SELECT has_schema_privilege('public', 'CREATE')")).scalar()
        extra = c.execute(text(
            "SELECT c.relname, p.priv FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN unnest(ARRAY['TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p(priv) "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND has_table_privilege(:r, c.oid, p.priv)"),
            {"r": role}).all()
        assert extra == []


# ------------------------------------------------------------------------------------------------ concurrent revocation
@pytest.fixture(scope="module")
def pg_user(client, pg):
    from app.auth import DEMO_PASSWORD, hash_password
    from app.db import AdminSession
    from app.models import Actor, AuthCredential
    with AdminSession() as db:
        if db.get(Actor, PG_USER) is None:
            add_principal(db, actor_id=PG_USER, tenant_id=TENANT, actor_type="HUMAN", display_name=PG_USER,
                          email=PG_EMAIL, roles=["CCO"])
        db.merge(AuthCredential(actor_id=PG_USER, password_hash=hash_password(DEMO_PASSWORD), failed_attempts=0,
                                locked_until=None, must_change=False))
        db.commit()
    return DEMO_PASSWORD


def _request_session(token):
    """A request-shaped SessionLocal session, authenticated as get_ctx does."""
    from app.auth import resolve_token
    from app.db import SessionLocal, capability
    db = SessionLocal()
    with capability(db, "auth"):
        actor, sess = resolve_token(db, token)
    db.info["revalidate_session"] = True
    return db, actor, sess


def test_a_revocation_waits_for_an_in_flight_commit_and_the_next_commit_is_refused(client, pg, pg_user):
    from fastapi import HTTPException
    from app.db import AdminSession, admin_engine
    from app.models import AuditEvent, AuthSession
    from app.platform import audit
    token = client.post("/api/auth/login", json={"email": PG_EMAIL, "password": pg_user}).json()["token"]
    db, actor, sess = _request_session(token)
    try:
        # The in-flight request reaches its commit-time check, which share-locks its own session row (allowed without
        # the auth capability: session_self / session_self_lock) ...
        db.execute(select(AuthSession.revoked_at).where(AuthSession.session_id == sess.session_id).with_for_update(read=True))
        # ... so a concurrent revocation cannot commit underneath it: it waits (here: gives up after 300 ms).
        with admin_engine.connect() as other:
            tx = other.begin()
            other.exec_driver_sql("SET LOCAL lock_timeout = '300ms'")
            with pytest.raises(OperationalError, match="lock timeout"):
                other.execute(update(AuthSession).where(AuthSession.session_id == sess.session_id)
                              .values(revoked_at=text("now()")))
            tx.rollback()
        # ... and the request itself cannot un-revoke or extend its session (WITH CHECK false without `auth`)
        sp = db.begin_nested()
        with pytest.raises(ProgrammingError, match="row-level security"):
            db.execute(update(AuthSession).where(AuthSession.session_id == sess.session_id)
                       .values(expires_at=text("now() + interval '1 year'")))
        sp.rollback()
        audit(db, action="t2.pg_before_revoke", object_type="Probe", object_id=sess.session_id, actor=actor,
              tenant_id=actor.tenant_id)
        db.commit()  # authorised and still live at commit
    finally:
        db.close()
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    db = None
    try:
        from app.db import SessionLocal, set_principal
        db = SessionLocal()
        set_principal(db, TENANT, PG_USER, sess.session_id)  # authenticated just before the logout committed
        db.info["revalidate_session"] = True
        audit(db, action="t2.pg_after_revoke", object_type="Probe", object_id=sess.session_id, actor=None, tenant_id=TENANT)
        with pytest.raises(HTTPException) as refused:
            db.commit()
        assert refused.value.status_code == 401
    finally:
        db.close()
    with AdminSession() as s:
        actions = set(s.execute(select(AuditEvent.action).where(AuditEvent.object_id == sess.session_id)).scalars())
    assert actions == {"t2.pg_before_revoke"}


# ------------------------------------------------------------------------------------------------ API start-up
def test_api_start_up_needs_no_migration_credential(pg, monkeypatch):
    """DAT-REQ-004 / Task 2: every start-up check runs as the runtime role. With the migration engine unusable the API
    still starts; with a wrong context key it refuses."""
    from fastapi.testclient import TestClient
    from app import db, pg_bootstrap
    from app.main import app
    monkeypatch.setattr(db, "admin_engine", None)
    monkeypatch.setattr(db, "ADMIN_DATABASE_URL", "")
    monkeypatch.setattr(pg_bootstrap, "ADMIN_DATABASE_URL", "")
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
    monkeypatch.setattr(db, "_context_key_cache", secrets.token_bytes(32))
    with pytest.raises(pg_bootstrap.RuntimeRoleError, match="context key"):
        with TestClient(app):
            pass
