"""Completed results and the published catalogue are immutable below the application (audit I-02; roadmap Stage 2c;
migrations 0003 and 0004).

Requirements: DAT-REQ-011, STA-REQ-019, APP-REQ-012, IPE-REQ-025, IPE-INV-11, IPE-REQ-034, EVD-REQ-002, EVD-REQ-009,
EVD-REQ-026, DAT-REQ-035, DAT-REQ-036, API-REQ-019, SEC-REQ-029; DAT-REQ-012, RUL-REQ-009, RUL-REQ-014; and ZA-DAT-001
(completed evaluation/snapshot tables deny ordinary UPDATE/DELETE; the runtime role cannot update/delete PUBLISHED
catalogue artifacts; no runtime role may TRUNCATE authoritative tenant tables). PostgreSQL only.

The two layers are tested separately: the runtime role's privileges (refused before any row is read), and the row
trigger, which also stops the owner role's UPDATE and DELETE (the owner can still disable it with DDL; audit A10). Every write runs in a transaction that is rolled back; the migration test
works in a throwaway schema.
"""
from __future__ import annotations

import re
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

RUNTIME = "assure_app"
TENANT = "tenant-zoiko-group"  # the synthetic Customer Zero tenant the suite seeds

# The reviewed classification. The generated test fails whenever the database differs from it, including for a table
# added later without an explicit decision about its runtime-role privileges.
IMMUTABLE = {
    "applicability_evaluation",      # DAT-REQ-011, APP-REQ-012, API-REQ-019
    "assurance_control_evaluation",  # DAT-REQ-011, STA-REQ-019
    "assurance_snapshot",            # DAT-REQ-011, STA-REQ-019
    "evidence_ipe_evaluation",       # IPE-REQ-025, IPE-INV-11
    "evidence_package",              # EVD-REQ-026
    "governance_attestation",        # DAT-REQ-036, API-REQ-019
    "evidence_ledger_checkpoint",    # EVD-REQ-009
    # PUBLISHED catalogue artefacts (migration 0004): DAT-REQ-012 and ZA-DAT-001's "runtime role cannot update/delete
    # ... PUBLISHED catalogue artifacts"
    "catalog_rule_version",          # RUL-REQ-009, RUL-REQ-010
    "catalog_content_release",       # RUL-REQ-027
    "catalog_controlled_set_version",  # RUL-REQ-014
    "catalog_fact_definition",       # "Released version immutable"; APP-REQ-005
}
APPEND_ONLY = {
    "evidence_ledger_event",
    "platform_audit_event",  # trigger since 0002; EVD-REQ-002, IAM-REQ-023
    "assurance_exception_event",
    "assurance_compensation_event",
    "assurance_issue_event",
    "evidence_legal_hold_event",
    "evidence_conflict_event",
    "assurance_obligation_occurrence_event",
    "iam_role_assignment_event",  # Stage 4b, migration 0009: role assignment history (IAM-INV-09, IAM §22)
}
GUARDED = {  # columns a legitimate code path still writes in place; everything else in the row is immutable
    "evidence_source_run": {"lifecycle", "completed_at", "evidence_ids"},       # IPE-INV-11, DAT-REQ-035
    "evidence_ipe_anomaly": {"disposition_code", "disposition_rationale"},      # IPE-REQ-025, IPE-REQ-034 (interim)
    # Stage 4b, migration 0009: a role assignment changes only by closure (IAM §22 "revocation creates closure")
    "iam_role_assignment": {"status", "valid_to", "revoked_at", "revoked_by", "revoke_reason", "revoked_record_seq"},
    # Migration 0010: Stage 3c state projections change only in the lifecycle columns their event writer sets
    # (app/engines/governance.py); every other column is immutable and nothing may DELETE (DAT-REQ-009, EVD-REQ-018).
    "assurance_exception_revision": {"state", "approved_by"},
    "assurance_compensation_revision": {"status", "approved_by"},
    "assurance_issue": {"state", "owner_actor_id"},
    "evidence_legal_hold_revision": {"status", "released_at"},
    "evidence_conflict": {"status", "disposition"},
    "assurance_obligation_occurrence": {"fulfilled_at", "evidence_ids"},
}
PROTECTED = IMMUTABLE | APPEND_ONLY | set(GUARDED)
NO_RUNTIME_ACCESS = {"assure_context_key"}  # migration 0007: the context key pads; owner-only, no runtime privilege
# Legitimate completion writes, and a write to a protected column of the same row, for the guarded tables.
COMPLETION = {
    "evidence_source_run": ("lifecycle = 'COMPLETED', completed_at = now(), evidence_ids = CAST('[\"za-probe\"]' AS json)",
                            "ipe_policy_version_id = 'za-tampered-policy'"),
    "evidence_ipe_anomaly": ("disposition_code = 'FALSE_POSITIVE', disposition_rationale = 'probe'",
                             "affected_count = affected_count + 1"),
    "iam_role_assignment": ("status = 'REVOKED', valid_to = valid_from, revoked_at = now(), revoked_by = 'probe', "
                            "revoke_reason = 'probe', revoked_record_seq = 1",
                            "role_code = 'SUPER_ADMIN'"),
    "assurance_exception_revision": ("state = 'APPROVED', approved_by = 'probe'", "reason = 'tampered'"),
    "assurance_compensation_revision": ("status = 'APPROVED', approved_by = 'probe'", "equivalence_rationale = 'tampered'"),
    "assurance_issue": ("state = 'TRIAGED', owner_actor_id = 'probe'", "severity = 'LOW'"),
    "evidence_legal_hold_revision": ("status = 'RELEASED', released_at = now()", "authority = 'tampered'"),
    "evidence_conflict": ("status = 'RESOLVED', disposition = 'probe'", "description = 'tampered'"),
    "assurance_obligation_occurrence": ("fulfilled_at = now(), evidence_ids = CAST('[\"za-probe\"]' AS json)",
                                        "due_at = due_at + interval '1 day'"),  # a real change (now() = insert time)
}
# Probe values a table's CHECK constraints need (the generic probe fills text columns with a tag).
PROBE_VALUES = {
    "iam_role_assignment": lambda tenant: {"scope_type": "TENANT", "scope_id": tenant, "tenant_id": tenant,
                                           "status": "ACTIVE", "valid_to": None},
}

_PRIVILEGES_SQL = """
SELECT c.relname,
       has_table_privilege(:r, c.oid, 'SELECT'), has_table_privilege(:r, c.oid, 'INSERT'),
       has_table_privilege(:r, c.oid, 'UPDATE'), has_table_privilege(:r, c.oid, 'DELETE'),
       has_table_privilege(:r, c.oid, 'TRUNCATE'), has_table_privilege(:r, c.oid, 'REFERENCES'),
       has_table_privilege(:r, c.oid, 'TRIGGER'),
       ARRAY(SELECT a.attname::text FROM pg_attribute a
             WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
               AND has_column_privilege(:r, c.oid, a.attnum, 'UPDATE') ORDER BY 1),
       ARRAY(SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal ORDER BY 1)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :s AND c.relkind = 'r' AND c.relname <> 'alembic_version'
ORDER BY 1"""
# pg_get_triggerdef output: a row-level BEFORE trigger on both events, and the guard function (schema-qualified outside
# the search_path) with its arguments.
_ROW_GUARD = re.compile(r"BEFORE DELETE OR UPDATE ON \S+ FOR EACH ROW EXECUTE FUNCTION (?:\w+\.)?"
                        r"(assure_forbid_mutation(?:_except)?)\((.*)\)$")


@pytest.fixture(scope="module")
def engines(client):  # the client fixture has migrated `public` to head and seeded Customer Zero
    from app.db import IS_POSTGRES, admin_engine, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine, engine


def _policy_mismatches(conn, schema: str) -> list[str]:
    """Every difference between the runtime role's privileges and triggers in `schema` and the reviewed classification."""
    rows = conn.execute(text(_PRIVILEGES_SQL), {"r": RUNTIME, "s": schema}).all()
    seen, out = {r[0] for r in rows}, []
    out += [f"{t}: table missing" for t in sorted(PROTECTED - seen)]
    for t, sel, ins, upd, dele, trunc, refs, trig, upd_cols, triggers in rows:
        if t in NO_RUNTIME_ACCESS:
            if sel or ins or upd or dele or trunc or refs or trig or upd_cols:
                out.append(f"{t}: runtime role holds a privilege on an owner-only table")
            continue
        if not (sel and ins):
            out.append(f"{t}: runtime role lacks SELECT/INSERT")
        if trunc or refs or trig:
            out.append(f"{t}: runtime role holds TRUNCATE, REFERENCES or TRIGGER")  # ZA-DAT-001 §13
        guards = {m.group(1): set(re.findall(r"'(\w+)'", m.group(2))) for m in map(_ROW_GUARD.search, triggers) if m}
        if t in IMMUTABLE or t in APPEND_ONLY:
            if upd or dele or upd_cols:
                out.append(f"{t}: runtime role can UPDATE/DELETE (columns: {upd_cols})")
            if guards.get("assure_forbid_mutation") != set():
                out.append(f"{t}: append-only trigger missing ({triggers or 'no triggers'})")
        elif t in GUARDED:
            if upd or dele or set(upd_cols) != GUARDED[t]:
                out.append(f"{t}: runtime role UPDATE columns {upd_cols}, DELETE {dele}; expected only {sorted(GUARDED[t])}")
            if guards.get("assure_forbid_mutation_except") != GUARDED[t]:
                out.append(f"{t}: column guard trigger missing or wrong columns ({triggers or 'no triggers'})")
        elif not (upd and dele):
            out.append(f"{t}: unclassified table without UPDATE/DELETE; add it to the reviewed classification")
    return out


def _pk(conn, table: str) -> str:
    return conn.execute(text("SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                             "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary"),
                        {"t": table}).scalar_one()


def _probe_row(conn, table: str, **overrides) -> tuple[str, object]:
    """Insert a synthetic row: a value for every NOT NULL column without a server default. Returns (key column, key).
    A foreign-key column points at an existing row of the referenced table (the seeded catalogue)."""
    pk = _pk(conn, table)
    for col, ref_table, ref_col in conn.execute(text(
            "SELECT a.attname, cf.relname, af.attname FROM pg_constraint k "
            "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1] "
            "JOIN pg_class cf ON cf.oid = k.confrelid "
            "JOIN pg_attribute af ON af.attrelid = k.confrelid AND af.attnum = k.confkey[1] "
            "WHERE k.contype = 'f' AND k.conrelid = CAST(:t AS regclass)"), {"t": table}).all():
        overrides.setdefault(col, conn.execute(text(f'SELECT "{ref_col}" FROM "{ref_table}" LIMIT 1')).scalar_one())
    cols = conn.execute(text("SELECT column_name, data_type, character_maximum_length FROM information_schema.columns "
                             "WHERE table_schema = current_schema() AND table_name = :t AND is_nullable = 'NO' "
                             "AND column_default IS NULL ORDER BY ordinal_position"), {"t": table}).all()
    tag, names, exprs, params = "za-probe-" + secrets.token_hex(6), [], [], {}
    for name, dtype, maxlen in cols:
        names.append(name)
        if dtype.startswith("timestamp") or dtype == "date":
            exprs.append("now()" if dtype.startswith("timestamp") else "current_date")
            continue
        if dtype in ("json", "jsonb"):
            exprs.append(f"CAST(:{name} AS {dtype})")
            params[name] = overrides.get(name, "{}")
            continue
        exprs.append(f":{name}")
        if dtype in ("character varying", "text", "character"):
            params[name] = overrides.get(name, tag[:maxlen] if maxlen else tag)
        elif dtype in ("bigint", "integer", "smallint", "numeric"):
            params[name] = overrides.get(name, 0)
        elif dtype == "boolean":
            params[name] = overrides.get(name, False)
        else:
            raise AssertionError(f"{table}.{name}: no probe value for type {dtype}")
    for name, value in overrides.items():  # an override may also set a nullable column (e.g. a CHECK-bound scope)
        if name not in names:
            names.append(name)
            exprs.append(f":{name}")
            params[name] = value
    conn.execute(text(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(exprs)})"), params)
    return pk, params[pk]


def _refused(conn, sql: str, params: dict, match: str) -> None:
    """The statement fails with `match`; the surrounding transaction continues (savepoint)."""
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError, match=match):
        conn.execute(text(sql), params)
    sp.rollback()


# ---------------------------------------------------------------- both layers are present (generated over every table)
def test_runtime_role_privileges_and_triggers_match_the_reviewed_policy(engines):
    owner, _ = engines
    with owner.connect() as c:
        assert _policy_mismatches(c, "public") == []


# ---------------------------------------------------------------- layer 1: the runtime role is refused outright
def test_runtime_role_cannot_update_or_delete_completed_results(engines):
    owner, runtime = engines
    with owner.connect() as c:
        keys = {t: _pk(c, t) for t in PROTECTED}  # the primary key: a protected column in every table
    for t in sorted(PROTECTED):
        col = keys[t]
        for sql in (f"UPDATE {t} SET {col} = {col} WHERE false", f"DELETE FROM {t} WHERE false"):
            with runtime.connect() as c:
                # refused at the privilege check: no row is read, whatever the WHERE clause
                with pytest.raises(DBAPIError, match=f"permission denied for table {t}"):
                    c.execute(text(sql))
                c.rollback()


# ---------------------------------------------------------------- layer 2: the trigger stops the owner role as well
def test_completed_results_cannot_be_rewritten_even_by_the_owner_role(engines):
    owner, _ = engines
    for t in sorted(IMMUTABLE | APPEND_ONLY):
        with owner.connect() as c:
            tx = c.begin()
            try:
                pk, key = _probe_row(c, t)
                where = f"WHERE {pk} = :k"
                _refused(c, f"UPDATE {t} SET {pk} = {pk} {where}", {"k": key}, f"{t} is append-only")
                _refused(c, f"DELETE FROM {t} {where}", {"k": key}, f"{t} is append-only")
            finally:
                tx.rollback()


def test_guarded_rows_change_only_in_their_completion_columns(engines):
    owner, runtime = engines
    for t, (allowed, protected) in COMPLETION.items():
        # owner role: the trigger lets the completion columns through and nothing else
        with owner.connect() as c:
            tx = c.begin()
            try:
                pk, key = _probe_row(c, t, **PROBE_VALUES.get(t, lambda _t: {})("za-probe-tenant"))
                where = f"WHERE {pk} = :k"
                assert c.execute(text(f"UPDATE {t} SET {allowed} {where}"), {"k": key}).rowcount == 1
                _refused(c, f"UPDATE {t} SET {protected} {where}", {"k": key}, f"{t} is append-only except")
                _refused(c, f"DELETE FROM {t} {where}", {"k": key}, f"{t} is append-only")
            finally:
                tx.rollback()
        # runtime role, inside its tenant scope: the column privileges allow exactly the completion columns
        with runtime.connect() as c:
            tx = c.begin()
            try:
                from conftest import stamp
                stamp(c, TENANT)  # the API's signed tenant context (migration 0007)
                pk, key = _probe_row(c, t, **{"tenant_id": TENANT, **PROBE_VALUES.get(t, lambda _t: {})(TENANT)})
                where = f"WHERE {pk} = :k"
                assert c.execute(text(f"UPDATE {t} SET {allowed} {where}"), {"k": key}).rowcount == 1
                _refused(c, f"UPDATE {t} SET {protected} {where}", {"k": key}, f"permission denied for table {t}")
                _refused(c, f"DELETE FROM {t} {where}", {"k": key}, f"permission denied for table {t}")
            finally:
                tx.rollback()


def _verified_draft(client) -> tuple[dict, str]:
    """A republication draft of a current rule version, taken through every gate before release (author, peer, legal,
    shadow). Returns the rule version it supersedes and the draft ID."""
    from conftest import H
    base = next(v for v in client.get("/api/rules/versions", headers=H("actor-content-analyst")).json()
                if v["superseded_by"] is None)
    draft = client.post("/api/rules/drafts", headers=H("actor-content-analyst"), json={
        "obligation_definition_id": base["obligation_definition_id"], "authoring_mode": "AST",
        "ast": base["canonical_ast"], "golden_vectors": base["golden_vectors"], "source_mappings": base["source_mappings"],
        "valid_from": base["valid_from"], "change_class": "EXECUTABLE_OUTCOME"})
    assert draft.status_code == 200, draft.text
    did = draft.json()["draft_id"]
    for actor, step in (("actor-content-analyst", "submit-review"), ("actor-encoder", "peer-approve"),
                        ("actor-content-legal", "legal-approve"), ("actor-release", "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return base, did


def test_rule_publication_writes_each_catalogue_row_once(client, engines):
    """The real publication path, through every gate (author, peer, legal, shadow, a distinct release manager), works
    against the guard: the RuleVersion and its signed ContentRelease are inserted with final values, the manifest binds
    exactly that RuleVersion and AST hash (RUL-REQ-027), and the published rows cannot be rewritten afterwards."""
    from conftest import H
    owner, _ = engines
    base, did = _verified_draft(client)
    pub = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                      json={"label": "catalogue-immutability-probe", "changelog": "Republication of an unchanged AST"})
    assert pub.status_code == 200, pub.text
    rv, cr = pub.json()["rule_version"], pub.json()["content_release"]
    assert rv["content_release_id"] == cr["content_release_id"]
    assert [(m["rule_version_id"], m["ast_sha256"]) for m in cr["manifest"]["rule_versions"]] == [(rv["rule_version_id"],
                                                                                                 rv["ast_hash"])]
    assert rv["version"] == base["version"] + 1
    with owner.connect() as c:
        tx = c.begin()
        try:
            for table, key, value in (("catalog_rule_version", "rule_version_id", rv["rule_version_id"]),
                                      ("catalog_content_release", "content_release_id", cr["content_release_id"])):
                _refused(c, f"UPDATE {table} SET status = 'WITHDRAWN' WHERE {key} = :k", {"k": value},
                         f"{table} is append-only")
        finally:
            tx.rollback()


def test_failed_publication_leaves_no_catalogue_rows(client, engines, monkeypatch):
    """Publication fails closed and atomically (RUL-REQ-020, DAT-REQ-014): when signing fails after the RuleVersion is
    already flushed, nothing of the attempt survives (rule version, release, audit record) and the draft stays VERIFIED.
    A retry then publishes normally, with no version gap left by the failed attempt."""
    from conftest import H
    from app.engines import keys
    owner, _ = engines
    base, did = _verified_draft(client)
    label = f"failed-publication-{secrets.token_hex(4)}"

    def fail(_payload):
        raise RuntimeError("signing service unavailable")

    with monkeypatch.context() as m:
        m.setattr(keys, "sign", fail)
        with pytest.raises(RuntimeError, match="signing service unavailable"):
            client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                        json={"label": label, "changelog": "Signing fails after the rule version is flushed"})
    with owner.connect() as c:
        assert c.execute(text("SELECT count(*) FROM catalog_rule_version WHERE draft_id = :d"), {"d": did}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM catalog_content_release WHERE label = :l"), {"l": label}).scalar() == 0
        assert c.execute(text("SELECT status FROM catalog_rule_draft WHERE draft_id = :d"), {"d": did}).scalar() == "VERIFIED"
        assert c.execute(text("SELECT count(*) FROM platform_audit_event WHERE action = 'content.publish' "
                              "AND object_id NOT IN (SELECT content_release_id FROM catalog_content_release)")).scalar() == 0
        c.rollback()
    pub = client.post(f"/api/rules/drafts/{did}/publish", headers=H("actor-release"),
                      json={"label": label, "changelog": "Retry after the signing failure"})
    assert pub.status_code == 200, pub.text
    assert pub.json()["rule_version"]["version"] == base["version"] + 1


# ---------------------------------------------------------------- the migration is reversible, exactly
@pytest.fixture
def schema(engines):
    owner, _ = engines
    name = "za_imm_" + secrets.token_hex(4)
    with owner.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    yield name
    with owner.begin() as c:
        c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')  # this test's throwaway schema only


def test_downgrade_to_the_baseline_restores_its_privileges_exactly(engines, schema):
    from app import migrate
    owner, _ = engines
    migrate.upgrade(schema=schema, provision_role=False)
    with owner.connect() as c:
        assert _policy_mismatches(c, schema) == []
    migrate.downgrade(migrate.BASELINE_REVISION, confirm=True, schema=schema)
    with owner.connect() as c:
        try:
            fp = migrate.fingerprint(c, schema)
            column_acls = c.execute(text(
                "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s AND a.attacl IS NOT NULL"),
                {"s": schema}).scalar()
        finally:
            c.rollback()
    assert migrate.compare_fingerprints(migrate.load_baseline()["fingerprint"], fp) == []  # grants, triggers, functions
    assert column_acls == 0  # the column-level grants are gone too (the fingerprint does not cover them)
    migrate.upgrade(schema=schema, provision_role=False)
    with owner.connect() as c:
        assert _policy_mismatches(c, schema) == []
