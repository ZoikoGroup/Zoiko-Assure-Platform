"""Sessions are found by the SHA-256 of an opaque random bearer token (ZA-IAM-001 §05, §22 iam.session).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Before: a token was ``base64(session_id, actor_id, expiry) . HMAC-SHA256`` under a key kept in a local file
(``var/keys/session.key``). The database held everything except that key needed to re-create any live token.

After: a token is 32 random bytes returned once at sign-in. ``auth_session.token_hash`` keeps only its SHA-256; the
token is never stored or logged, and nothing in the database or on disk can re-create it. Lookup is by the unique hash.

Expand only: one nullable column and its unique index; table grants already cover the new column. Sessions created
before this revision have no hash, so their tokens stop working and their users sign in again. No row is rewritten.

Downgrade drops the column and index. Tokens issued after the upgrade then stop working (sign in again).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_session", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_auth_session_token_hash"), "auth_session", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_session_token_hash"), table_name="auth_session")
    op.drop_column("auth_session", "token_hash")
