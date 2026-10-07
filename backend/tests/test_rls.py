"""PostgreSQL row-level security proofs (skipped on SQLite).

These talk to the database directly as the runtime role, bypassing every application-layer
check, to show the database itself enforces tenant isolation (INV-14, DAT-REQ-005).
"""
import pytest
from sqlalchemy import text


@pytest.fixture(scope="module")
def pg(client):  # client fixture ensures bootstrap + seed have run
    from app.db import IS_POSTGRES, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return engine


def _scoped(conn, tenant, system="off"):
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.system', :s, true)"),
                 {"t": tenant, "s": system})


def test_runtime_role_is_not_owner_and_cannot_bypass_rls(pg):
    with pg.connect() as c:
        row = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert row == (False, False)
        owner = c.execute(text("SELECT tableowner FROM pg_tables WHERE tablename = 'evidence_artifact'")).scalar()
        assert owner != c.execute(text("SELECT current_user")).scalar()


def test_no_tenant_scope_sees_no_tenant_rows(pg):
    with pg.begin() as c:
        _scoped(c, "")
        for t in ("evidence_artifact", "applicability_evaluation", "assurance_snapshot", "org_legal_entity"):
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0, t


def test_tenant_scope_sees_only_own_rows(pg):
    with pg.begin() as c:
        _scoped(c, "tenant-zoiko-group")
        n = c.execute(text("SELECT count(*) FROM evidence_artifact")).scalar()
        assert n > 0
        assert c.execute(text("SELECT count(*) FROM evidence_artifact WHERE tenant_id <> 'tenant-zoiko-group'")).scalar() == 0
    with pg.begin() as c:
        _scoped(c, "tenant-someone-else")
        assert c.execute(text("SELECT count(*) FROM evidence_artifact")).scalar() == 0


def test_cross_tenant_write_is_rejected(pg):
    from sqlalchemy.exc import ProgrammingError
    with pg.connect() as c:
        tx = c.begin()
        _scoped(c, "tenant-someone-else")
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(text("INSERT INTO assurance_issue (issue_id, tenant_id, title, issue_type, severity, priority, state, dedupe_key, created_at) "
                           "VALUES ('rls-probe', 'tenant-zoiko-group', 'x', 'PROOF_GAP', 'LOW', 'P4', 'OPEN', 'rls-probe', now())"))
        tx.rollback()


def test_ledger_is_append_only_even_for_its_tenant(pg):
    from sqlalchemy.exc import InternalError, ProgrammingError
    with pg.connect() as c:
        tx = c.begin()
        _scoped(c, "tenant-zoiko-group")
        with pytest.raises((InternalError, ProgrammingError), match="append-only"):
            c.execute(text("UPDATE evidence_ledger_event SET reason = 'tampered'"))
        tx.rollback()
