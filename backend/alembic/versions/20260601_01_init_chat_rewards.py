"""init chat and rewards tables

Revision ID: 20260601_01
Revises:
Create Date: 2026-06-01 18:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260601_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_sessions_account_id", "chat_sessions", ["account_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lesson_id", sa.String(length=128), nullable=True),
        sa.Column("doc_chunk_ids_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"], unique=False)
    op.create_index("ix_chat_messages_session_seq", "chat_messages", ["session_id", "seq_no"], unique=False)

    op.create_table(
        "reward_sessions",
        sa.Column("session_id", sa.String(length=128), primary_key=True),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reward_sessions_account_id", "reward_sessions", ["account_id"], unique=False)

    op.create_table(
        "reward_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=128), sa.ForeignKey("reward_sessions.session_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_points", sa.Integer(), nullable=False),
        sa.Column("total_quizzes", sa.Integer(), nullable=False),
        sa.Column("total_correct", sa.Integer(), nullable=False),
        sa.Column("total_answered", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_reward_events_session_id", "reward_events", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reward_events_session_id", table_name="reward_events")
    op.drop_table("reward_events")
    op.drop_index("ix_reward_sessions_account_id", table_name="reward_sessions")
    op.drop_table("reward_sessions")
    op.drop_index("ix_chat_messages_session_seq", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_account_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
