"""add consecutive_lockouts to account keys

Revision ID: 20260602_03
Revises: 20260602_02
Create Date: 2026-06-02 15:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_03"
down_revision = "20260602_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_keys",
        sa.Column("consecutive_lockouts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("account_keys", "consecutive_lockouts", server_default=None)


def downgrade() -> None:
    op.drop_column("account_keys", "consecutive_lockouts")
