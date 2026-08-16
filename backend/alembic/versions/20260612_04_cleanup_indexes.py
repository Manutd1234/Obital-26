"""add cleanup/scoping indexes

Revision ID: 20260612_04
Revises: 20260612_03
Create Date: 2026-06-12 23:59:00
"""

from alembic import op

revision = "20260612_04"
down_revision = "20260612_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_expires_at ON account_unlock_sessions (expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_artifacts_deleted_at ON account_artifacts (deleted_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_artifacts_expires_at ON account_artifacts (expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bin_items_expires_at ON bin_items (expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_bin_items_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_deleted_at")
    op.execute("DROP INDEX IF EXISTS ix_account_unlock_sessions_expires_at")
