"""add account unlock sessions table

Revision ID: 20260602_02
Revises: 20260602_01
Create Date: 2026-06-02 10:35:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_02"
down_revision = "20260602_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_unlock_sessions",
        sa.Column("token_hash", sa.String(length=128), primary_key=True),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("dek_ciphertext_b64", sa.Text(), nullable=False),
        sa.Column("unlock_version", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_unlock_sessions_account_id", "account_unlock_sessions", ["account_id"])
    op.alter_column("account_unlock_sessions", "client_id", server_default=None)
    op.alter_column("account_unlock_sessions", "user_agent", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_account_unlock_sessions_account_id", table_name="account_unlock_sessions")
    op.drop_table("account_unlock_sessions")
