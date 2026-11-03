"""Alembic environment for the Zoiko Assure schema (ZA-DAT-001 DAT-REQ-023/039; docs/database-migrations.md).

Migrations run with the owner/migration role (DATABASE_ADMIN_URL), never the runtime role. ``python -m app.migrate``
is the supported entry point: it passes its own connection (already inside one transaction holding the migration
lock) through ``config.attributes``, so the whole run commits or rolls back as a unit.

Run from the plain ``alembic`` command line, this environment allows the authoring and read-only commands
(``revision --autogenerate``, ``check``, ``current``, ``history``, ``heads``) and refuses ``upgrade``, ``downgrade`` and
``stamp``, which could otherwise record a version on a database nobody has validated.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

os.environ.setdefault("ZA_ADMIN_TOOL", "1")  # migration tooling: backend/.env may supply DATABASE_ADMIN_URL
from app import models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.db import ADMIN_DATABASE_URL, Base  # noqa: E402

config = context.config
target_metadata = Base.metadata
SCHEMA = config.attributes.get("schema", "public")
_GUARDED = {"upgrade", "downgrade", "stamp"}


def _refuse_unguarded_cli() -> None:
    cmd = getattr(config.cmd_opts, "cmd", None)
    name = getattr(cmd[0], "__name__", "") if cmd else ""
    if name in _GUARDED and not config.attributes.get("za_migrate_tool"):
        raise SystemExit(f"Refusing `alembic {name}`: use `python -m app.migrate` (it validates the database, takes the "
                         "migration lock and runs in one transaction). See docs/database-migrations.md.")


def _include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in ("alembic_version", "assure_context_key"))


def _configure(**kw) -> None:
    context.configure(target_metadata=target_metadata, version_table_schema=SCHEMA, include_object=_include_object,
                      compare_type=True, transaction_per_migration=False, **kw)


def run_migrations_offline() -> None:
    """`alembic upgrade --sql`: renders reviewable SQL without touching a database."""
    _configure(url=ADMIN_DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    # Unqualified object names resolve to the target schema (public in every real database; a throwaway schema in
    # the migration tests). SET LOCAL ends with the transaction.
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{SCHEMA}"')
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _refuse_unguarded_cli()
    connection = config.attributes.get("connection")
    if connection is not None:  # supplied by app.migrate inside its own transaction
        _run(connection)
        return
    engine = create_engine(ADMIN_DATABASE_URL)
    try:
        with engine.connect() as conn:
            with conn.begin():
                _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
