"""add unique constraint for chat message sequence

Revision ID: 20260601_02
Revises: 20260601_01
Create Date: 2026-06-01 20:05:00
"""

from alembic import op


revision = "20260601_02"
down_revision = "20260601_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_chat_messages_session_seq", "chat_messages", ["session_id", "seq_no"])


def downgrade() -> None:
    op.drop_constraint("uq_chat_messages_session_seq", "chat_messages", type_="unique")
