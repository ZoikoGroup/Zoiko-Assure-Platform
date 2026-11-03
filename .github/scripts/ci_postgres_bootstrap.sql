-- Disposable CI database for the backend-postgres job in .github/workflows/ci.yml (see docs/ci-cd.md).
--
-- Runs as the superuser of a throwaway postgres:17 container, over that container's local socket, and nowhere
-- else. Never point it at a shared, staging or production server.
--
-- The owner password arrives through the ZA_CI_OWNER_PASSWORD environment variable (generated per run and masked
-- in the log). It is never placed on a command line and psql does not echo it.
\set ON_ERROR_STOP on
\getenv owner_password ZA_CI_OWNER_PASSWORD

-- Owner/migration role, least privilege as the backend's checks require (DAT-REQ-004/027, SEC-REQ-023):
-- NOSUPERUSER + CREATEROLE. It owns the test databases; `python -m app.migrate upgrade` uses it to apply the
-- versioned migrations (tables, RLS, append-only triggers) and to provision the runtime role assure_app. The RLS
-- policies name assure_app, so that role name is fixed.
CREATE ROLE assure WITH LOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'owner_password';

-- Every pytest session needs a database of its own: the suite stores evidence payloads in a per-session temporary
-- directory, so a database reused by a second session points at files that no longer exist. Two fresh databases
-- avoid any reset (no DROP anywhere in CI):
--   assure_test_rls  the dedicated, fail-fast run of tests/test_rls.py
--   assure_test      the full backend suite (which runs the RLS proofs again)
CREATE DATABASE assure_test_rls OWNER assure;
CREATE DATABASE assure_test OWNER assure;
