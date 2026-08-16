"""add chat session titles

Revision ID: 20260614_01
Revises: 20260612_04
Create Date: 2026-06-14 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260614_01"
down_revision = "20260612_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("chat_sessions")}
    if "title" not in columns:
        op.add_column(
            "chat_sessions",
            sa.Column("title", sa.Text(), nullable=False, server_default=""),
        )
        op.alter_column("chat_sessions", "title", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("chat_sessions")}
    if "title" in columns:
        op.drop_column("chat_sessions", "title")
