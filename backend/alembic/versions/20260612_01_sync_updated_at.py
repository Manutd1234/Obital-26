"""add updated_at to chat and reward sync models

Revision ID: 20260612_01
Revises: 20260602_03
Create Date: 2026-06-12 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260612_01"
down_revision = "20260602_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.func.now()
    for table in ("chat_sessions", "chat_messages", "reward_sessions", "reward_events"):
        op.add_column(
            table,
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        )
        op.alter_column(table, "updated_at", server_default=None)


def downgrade() -> None:
    for table in ("reward_events", "reward_sessions", "chat_messages", "chat_sessions"):
        op.drop_column(table, "updated_at")
