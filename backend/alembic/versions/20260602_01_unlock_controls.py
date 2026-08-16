"""add unlock controls to account keys

Revision ID: 20260602_01
Revises: 20260601_04
Create Date: 2026-06-02 09:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_01"
down_revision = "20260601_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_keys", sa.Column("unlock_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "account_keys",
        sa.Column("failed_unlock_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("account_keys", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("account_keys", sa.Column("last_failed_unlock_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("account_keys", "unlock_version", server_default=None)
    op.alter_column("account_keys", "failed_unlock_attempts", server_default=None)


def downgrade() -> None:
    op.drop_column("account_keys", "last_failed_unlock_at")
    op.drop_column("account_keys", "locked_until")
    op.drop_column("account_keys", "failed_unlock_attempts")
    op.drop_column("account_keys", "unlock_version")
