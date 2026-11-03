"""DAT §13 roles and non-login ownership, FORCE ROW LEVEL SECURITY, and a database-verified tenant context (I-01).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Finding I-01: the policies trusted ``app.tenant_id`` / ``app.system``, plain settings any session can change, so anything
that could run SQL as the runtime role (including injected SQL inside one SELECT) could choose a tenant or bypass RLS.

1. **Verified context** (ZA-DAT-001 §12, DAT-ADR-02). The API stamps each transaction with ``app.context`` =
   ``v1|tenant|actor|session|flags|nonce|mac``: the nonce is ``assure_context_nonce()`` (backend PID + transaction start,
   so a context is valid in exactly one transaction) and the mac is HMAC-SHA256 under a key the runtime role cannot
   read (``assure_context_key``: the HMAC inner/outer pads, owner-only; ``python -m app.migrate upgrade`` provisions it
   from the key the API holds). ``assure_context()`` is the one ``SECURITY DEFINER`` verifier (fixed search_path,
   non-login owner; DAT-REQ-027, SEC-REQ-045). ``assure_current_tenant()`` and ``assure_system()`` keep their OIDs, so
   policies keep pointing at them, but now return only verified values; an unsigned, forged, replayed or missing
   context means no tenant, no system scope and no platform rows (fail closed). Policies call them as ``(SELECT …)``
   so they run once per statement.
2. **Roles** (ZA-DAT-001 §13; NOLOGIN, created once per cluster): ``assure_table_owner`` (DAT "table_owner roles:
   non-login owners per schema"; the name is this implementation's), ``assure_migrator``, ``assure_app_rw``,
   ``assure_app_ro``, ``assure_outbox_publisher``, ``assure_catalog_loader``, ``assure_analytics_ro``,
   ``assure_breakglass_admin``. The login roles keep their names and credentials: the migration login (session user)
   becomes a member of ``assure_migrator``; the runtime login ``assure_app`` becomes a member of ``assure_app_rw``
   (INHERIT, no SET), which now holds exactly the privileges ``assure_app`` held. Every object in the schema and the
   schema itself are owned by ``assure_table_owner``.
3. **FORCE ROW LEVEL SECURITY** on every RLS table (DAT-REQ-003, TEN-REQ-022), so the owner is subject to policies
   too. ``migrator_all`` is the one explicitly privileged path (DAT §12: "except through explicitly audited privileged
   roles"): migrations, provisioning and operator tooling run as ``assure_migrator``.
4. **Identity tables.** ``org_actor`` is no longer exempt: tenant rows by tenant, Zoiko staff rows to verified
   platform principals, and sign-in identity discovery only under the signed ``auth`` capability (ZA-IAM-001 §22: "a
   separately controlled lookup path"). ``auth_session`` and ``auth_credential`` are readable and writable only under
   that capability, except that a request may read and share-lock its own session row (commit-time revalidation).
5. The runtime roles may read ``alembic_version`` (not change it), so API start-up needs no owner credential.

Roles and memberships are cluster-wide; downgrade leaves them in place (other databases on the cluster may use them)
and restores this schema exactly as 0006 left it: policies, functions, grants to ``assure_app`` and ownership by the
migrating login.
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

RUNTIME_LOGIN = "assure_app"
OWNER, MIGRATOR, APP_RW, APP_RO = "assure_table_owner", "assure_migrator", "assure_app_rw", "assure_app_ro"
PUBLISHER, LOADER, ANALYTICS, BREAKGLASS = ("assure_outbox_publisher", "assure_catalog_loader", "assure_analytics_ro",
                                            "assure_breakglass_admin")
ROLES = (OWNER, MIGRATOR, APP_RW, APP_RO, PUBLISHER, LOADER, ANALYTICS, BREAKGLASS)
PLATFORM_LOGS = ("platform_audit_event", "platform_outbox_event")
IDENTITY = ("auth_session", "auth_credential")
NO_READ_ONLY = ("auth_session", "auth_credential", "alembic_version", "assure_context_key")
PUBLISHER_COLUMNS = ("published_at", "attempts", "available_at")


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0006: `%` stays literal
        cur.execute(sql)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _array(values) -> str:
    return "ARRAY[" + ", ".join(map(_lit, values)) + "]::text[]"


def _functions(s: str) -> str:
    """The verifier and the helpers the policies call. Schema-qualified, search_path pinned to pg_catalog."""
    return f"""
CREATE FUNCTION {s}.assure_context_nonce() RETURNS text
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT pg_backend_pid()::text || '.' || floor(extract(epoch FROM transaction_timestamp()) * 1000000)::bigint::text $f$;

CREATE TABLE {s}.assure_context_key (
  id smallint PRIMARY KEY CHECK (id = 1),
  key_id text NOT NULL,
  k_ipad bytea NOT NULL CHECK (octet_length(k_ipad) = 64),
  k_opad bytea NOT NULL CHECK (octet_length(k_opad) = 64),
  provisioned_at timestamptz NOT NULL DEFAULT now());
REVOKE ALL ON TABLE {s}.assure_context_key FROM PUBLIC;

CREATE FUNCTION {s}.assure_context(OUT tenant_id text, OUT actor_id text, OUT session_id text, OUT flags text[])
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
  AS $f$
DECLARE
  raw text := current_setting('app.context', true);
  p text[];
  k record;
BEGIN
  IF raw IS NULL OR raw = '' THEN RETURN; END IF;
  p := string_to_array(raw, '|');
  IF coalesce(array_length(p, 1), 0) <> 7 OR p[1] <> 'v1' OR p[6] IS DISTINCT FROM {s}.assure_context_nonce() THEN
    RETURN;
  END IF;
  SELECT c.k_ipad, c.k_opad INTO k FROM {s}.assure_context_key c WHERE c.id = 1;
  IF NOT FOUND THEN RETURN; END IF;
  IF encode(sha256(k.k_opad || sha256(k.k_ipad || convert_to(array_to_string(p[1:6], '|'), 'UTF8'))), 'hex')
       IS DISTINCT FROM p[7] THEN
    RETURN;
  END IF;
  tenant_id := nullif(p[2], '');
  actor_id := nullif(p[3], '');
  session_id := nullif(p[4], '');
  flags := coalesce(string_to_array(nullif(p[5], ''), ','), '{{}}'::text[]);
END $f$;

CREATE OR REPLACE FUNCTION {s}.assure_current_tenant() RETURNS text
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT ({s}.assure_context()).tenant_id $f$;
CREATE OR REPLACE FUNCTION {s}.assure_system() RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT coalesce('system' = ANY (({s}.assure_context()).flags), false) $f$;
CREATE FUNCTION {s}.assure_context_has(capability text) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT coalesce(capability = ANY (({s}.assure_context()).flags), false) $f$;
CREATE FUNCTION {s}.assure_context_session() RETURNS text
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT ({s}.assure_context()).session_id $f$;
CREATE FUNCTION {s}.assure_platform_reader() RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT c.flags IS NOT NULL AND c.tenant_id IS NULL AND c.actor_id IS NOT NULL FROM {s}.assure_context() c $f$;
CREATE FUNCTION {s}.assure_platform_writer() RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog
  AS $f$ SELECT c.flags IS NOT NULL AND c.tenant_id IS NULL FROM {s}.assure_context() c $f$;
CREATE FUNCTION {s}.assure_context_key_id() RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
  AS $f$ SELECT key_id FROM {s}.assure_context_key WHERE id = 1 $f$;
"""


def upgrade() -> None:
    schema = _schema()
    s, ls = _q(schema), _lit(schema)
    tenant = "tenant_id = (SELECT assure_current_tenant()) OR (SELECT assure_system())"
    sql = f"""
-- 1. DAT §13 roles: NOLOGIN, no dangerous attributes; refuse if a same-named role already has any
DO $$
DECLARE r text;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ANY ({_array(ROLES)}) AND (rolcanlogin OR rolsuper OR rolbypassrls
             OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
    RAISE EXCEPTION 'a DAT §13 group role already exists with LOGIN or an elevated attribute; fix it before migrating';
  END IF;
  FOREACH r IN ARRAY {_array(ROLES)} LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', r);
    END IF;
  END LOOP;
END $$;
GRANT {OWNER} TO {MIGRATOR} WITH INHERIT TRUE, SET TRUE;
GRANT {MIGRATOR} TO SESSION_USER WITH INHERIT TRUE, SET TRUE;
GRANT {APP_RW} TO {RUNTIME_LOGIN} WITH INHERIT TRUE, SET FALSE;

-- 2. the runtime privileges move, unchanged, from the login role to assure_app_rw
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname, c.relkind, a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           CROSS JOIN LATERAL aclexplode(c.relacl) a
           WHERE n.nspname = {ls} AND a.grantee = '{RUNTIME_LOGIN}'::regrole LOOP
    EXECUTE format('GRANT %s ON %s %I.%I TO {APP_RW}', r.privilege_type,
                   CASE WHEN r.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, {ls}, r.relname);
  END LOOP;
  FOR r IN SELECT c.relname, at.attname, a.privilege_type FROM pg_attribute at JOIN pg_class c ON c.oid = at.attrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace CROSS JOIN LATERAL aclexplode(at.attacl) a
           WHERE n.nspname = {ls} AND at.attnum > 0 AND a.grantee = '{RUNTIME_LOGIN}'::regrole LOOP
    EXECUTE format('GRANT %s (%I) ON TABLE %I.%I TO {APP_RW}', r.privilege_type, r.attname, {ls}, r.relname);
  END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM {RUNTIME_LOGIN};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA {s} FROM {RUNTIME_LOGIN};
REVOKE ALL ON SCHEMA {s} FROM {RUNTIME_LOGIN};
GRANT USAGE ON SCHEMA {s} TO {APP_RW}, {APP_RO}, {PUBLISHER}, {LOADER};
GRANT SELECT ON TABLE {s}.alembic_version TO {APP_RW}, {APP_RO};

-- 3. the other DAT §13 roles, exactly as far as objects exist for them
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = {ls} AND c.relkind = 'r' AND c.relname <> ALL ({_array(NO_READ_ONLY)}) LOOP
    EXECUTE format('GRANT SELECT ON TABLE %I.%I TO {APP_RO}', {ls}, t);
  END LOOP;
  FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = {ls} AND c.relkind = 'r' AND c.relname LIKE 'catalog\\_%' LOOP
    EXECUTE format('GRANT SELECT, INSERT ON TABLE %I.%I TO {LOADER}', {ls}, t);
  END LOOP;
END $$;
GRANT SELECT ON TABLE {s}.platform_outbox_event TO {PUBLISHER};
GRANT UPDATE ({", ".join(PUBLISHER_COLUMNS)}) ON TABLE {s}.platform_outbox_event TO {PUBLISHER};

-- 4. verified context
{_functions(s)}

-- 5. policies and FORCE ROW LEVEL SECURITY
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = {ls} AND c.relkind = 'r' AND EXISTS (
             SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', {ls}, t);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', {ls}, t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I.%I', {ls}, t);
    IF t = 'org_tenant' THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I TO {APP_RW}, {APP_RO} USING (%s)', {ls}, t, {_lit(tenant)});
    ELSE
      EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I TO {APP_RW}, {APP_RO} USING (%s) WITH CHECK (%s)',
                     {ls}, t, {_lit(tenant)}, {_lit(tenant)});
    END IF;
    EXECUTE format('CREATE POLICY migrator_all ON %I.%I TO {MIGRATOR} USING (true) WITH CHECK (true)', {ls}, t);
  END LOOP;
  FOREACH t IN ARRAY {_array(PLATFORM_LOGS)} LOOP
    EXECUTE format('DROP POLICY platform_rows_select ON %I.%I', {ls}, t);
    EXECUTE format('DROP POLICY platform_rows_insert ON %I.%I', {ls}, t);
    EXECUTE format('CREATE POLICY platform_rows_select ON %I.%I FOR SELECT TO {APP_RW}, {APP_RO} '
                   'USING (tenant_id IS NULL AND (SELECT assure_platform_reader()))', {ls}, t);
    EXECUTE format('CREATE POLICY platform_rows_insert ON %I.%I FOR INSERT TO {APP_RW} '
                   'WITH CHECK (tenant_id IS NULL AND (SELECT assure_platform_writer()))', {ls}, t);
  END LOOP;
  FOREACH t IN ARRAY {_array(IDENTITY)} LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', {ls}, t);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', {ls}, t);
    EXECUTE format('CREATE POLICY auth_access ON %I.%I TO {APP_RW} USING ((SELECT assure_context_has(''auth''))) '
                   'WITH CHECK ((SELECT assure_context_has(''auth'')))', {ls}, t);
    EXECUTE format('CREATE POLICY migrator_all ON %I.%I TO {MIGRATOR} USING (true) WITH CHECK (true)', {ls}, t);
  END LOOP;
END $$;
CREATE POLICY session_self ON {s}.auth_session FOR SELECT TO {APP_RW}
  USING (session_id = (SELECT assure_context_session()));
CREATE POLICY session_self_lock ON {s}.auth_session FOR UPDATE TO {APP_RW}
  USING (session_id = (SELECT assure_context_session())) WITH CHECK (false);
CREATE POLICY platform_actors ON {s}.org_actor FOR SELECT TO {APP_RW}, {APP_RO}
  USING (tenant_id IS NULL AND (SELECT assure_platform_reader()));
CREATE POLICY identity_lookup ON {s}.org_actor FOR SELECT TO {APP_RW}
  USING ((SELECT assure_context_has('auth')));
CREATE POLICY outbox_publisher_read ON {s}.platform_outbox_event FOR SELECT TO {PUBLISHER} USING (true);
CREATE POLICY outbox_publisher_claim ON {s}.platform_outbox_event FOR UPDATE TO {PUBLISHER} USING (true) WITH CHECK (true);

-- 6. ownership: the non-login owner owns the schema (which needs CREATE on the database) and every object in it
DO $$ BEGIN EXECUTE format('GRANT CREATE ON DATABASE %I TO {OWNER}', current_database()); END $$;
ALTER SCHEMA {s} OWNER TO {OWNER};
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = {ls} AND c.relkind IN ('r', 'p', 'v', 'm', 'S') AND c.relowner <> '{OWNER}'::regrole LOOP
    EXECUTE format('ALTER %s %I.%I OWNER TO {OWNER}', CASE r.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                   WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END, {ls}, r.relname);
  END LOOP;
  FOR r IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = {ls} AND p.proowner <> '{OWNER}'::regrole
             AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                             AND d.deptype = 'e') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO {OWNER}', r.fn);
  END LOOP;
END $$;
"""
    _execute(sql)


def downgrade() -> None:
    schema = _schema()
    s, ls = _q(schema), _lit(schema)
    old = "tenant_id = assure_current_tenant() OR assure_system()"
    platform_0005 = "tenant_id IS NULL AND assure_current_tenant() IS NULL"
    schema_owner = "pg_database_owner" if schema == "public" else "SESSION_USER"
    sql = f"""
-- ownership back to the migrating login, as 0006 left it
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = {ls} AND c.relkind IN ('r', 'p', 'v', 'm', 'S') AND c.relowner = '{OWNER}'::regrole LOOP
    EXECUTE format('ALTER %s %I.%I OWNER TO %I', CASE r.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                   WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END, {ls}, r.relname, session_user);
  END LOOP;
  FOR r IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = {ls} AND p.proowner = '{OWNER}'::regrole LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', r.fn, session_user);
  END LOOP;
END $$;
ALTER SCHEMA {s} OWNER TO {schema_owner};
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = '{OWNER}'::regrole) THEN
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM {OWNER}', current_database());
  END IF;
END $$;

-- policies and RLS flags as 0006 left them
DROP POLICY outbox_publisher_claim ON {s}.platform_outbox_event;
DROP POLICY outbox_publisher_read ON {s}.platform_outbox_event;
DROP POLICY identity_lookup ON {s}.org_actor;
DROP POLICY platform_actors ON {s}.org_actor;
DROP POLICY session_self_lock ON {s}.auth_session;
DROP POLICY session_self ON {s}.auth_session;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY {_array(IDENTITY)} LOOP
    EXECUTE format('DROP POLICY auth_access ON %I.%I', {ls}, t);
    EXECUTE format('DROP POLICY migrator_all ON %I.%I', {ls}, t);
    EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY', {ls}, t);
    EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY', {ls}, t);
  END LOOP;
  FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = {ls} AND c.relkind = 'r' AND EXISTS (
             SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) LOOP
    EXECUTE format('DROP POLICY migrator_all ON %I.%I', {ls}, t);
    EXECUTE format('DROP POLICY tenant_isolation ON %I.%I', {ls}, t);
    EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY', {ls}, t);
    IF t = 'org_actor' THEN
      EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY', {ls}, t);
    ELSIF t = 'org_tenant' THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I TO {RUNTIME_LOGIN} USING (%s)', {ls}, t, {_lit(old)});
    ELSE
      EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I TO {RUNTIME_LOGIN} USING (%s) WITH CHECK (%s)',
                     {ls}, t, {_lit(old)}, {_lit(old)});
    END IF;
  END LOOP;
  FOREACH t IN ARRAY {_array(PLATFORM_LOGS)} LOOP
    EXECUTE format('DROP POLICY platform_rows_select ON %I.%I', {ls}, t);
    EXECUTE format('DROP POLICY platform_rows_insert ON %I.%I', {ls}, t);
    EXECUTE format('CREATE POLICY platform_rows_select ON %I.%I FOR SELECT TO {RUNTIME_LOGIN} USING (%s)',
                   {ls}, t, {_lit(platform_0005)});
    EXECUTE format('CREATE POLICY platform_rows_insert ON %I.%I FOR INSERT TO {RUNTIME_LOGIN} WITH CHECK (%s)',
                   {ls}, t, {_lit(platform_0005)});
  END LOOP;
END $$;

-- context functions as 0002 defined them
DROP FUNCTION {s}.assure_context_key_id();
DROP FUNCTION {s}.assure_platform_writer();
DROP FUNCTION {s}.assure_platform_reader();
DROP FUNCTION {s}.assure_context_session();
DROP FUNCTION {s}.assure_context_has(text);
CREATE OR REPLACE FUNCTION {s}.assure_current_tenant() RETURNS text
  LANGUAGE sql STABLE AS $f$ SELECT nullif(current_setting('app.tenant_id', true), '') $f$;
CREATE OR REPLACE FUNCTION {s}.assure_system() RETURNS boolean
  LANGUAGE sql STABLE AS $f$ SELECT coalesce(current_setting('app.system', true), 'off') = 'on' $f$;
DROP FUNCTION {s}.assure_context();
DROP TABLE {s}.assure_context_key;
DROP FUNCTION {s}.assure_context_nonce();

-- runtime privileges back on the login role
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname, c.relkind, a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           CROSS JOIN LATERAL aclexplode(c.relacl) a
           WHERE n.nspname = {ls} AND a.grantee = '{APP_RW}'::regrole AND c.relname <> 'alembic_version' LOOP
    EXECUTE format('GRANT %s ON %s %I.%I TO {RUNTIME_LOGIN}', r.privilege_type,
                   CASE WHEN r.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, {ls}, r.relname);
  END LOOP;
  FOR r IN SELECT c.relname, at.attname, a.privilege_type FROM pg_attribute at JOIN pg_class c ON c.oid = at.attrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace CROSS JOIN LATERAL aclexplode(at.attacl) a
           WHERE n.nspname = {ls} AND at.attnum > 0 AND a.grantee = '{APP_RW}'::regrole LOOP
    EXECUTE format('GRANT %s (%I) ON TABLE %I.%I TO {RUNTIME_LOGIN}', r.privilege_type, r.attname, {ls}, r.relname);
  END LOOP;
END $$;
GRANT USAGE ON SCHEMA {s} TO {RUNTIME_LOGIN};
REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM {APP_RW}, {APP_RO}, {PUBLISHER}, {LOADER};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA {s} FROM {APP_RW}, {APP_RO}, {PUBLISHER}, {LOADER};
REVOKE ALL ON SCHEMA {s} FROM {APP_RW}, {APP_RO}, {PUBLISHER}, {LOADER};
"""
    _execute(sql)
