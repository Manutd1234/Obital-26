"""add query path indexes

Revision ID: 20260617_01
Revises: 20260614_01
Create Date: 2026-06-17 00:00:00
"""

from alembic import op

revision = "20260617_01"
down_revision = "20260614_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_expires_at ON account_unlock_sessions (expires_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_account_expires_created "
        "ON account_unlock_sessions (account_id, expires_at, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_account_created_at "
        "ON account_unlock_sessions (account_id, created_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_artifacts_deleted_at ON account_artifacts (deleted_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_artifacts_expires_at ON account_artifacts (expires_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_account_deleted "
        "ON account_artifacts (account_id, deleted_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_account_expires "
        "ON account_artifacts (account_id, expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_hydration_queue "
        "ON account_artifacts (account_id, hydration_status, size_bytes, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_hydration_queue_ordered "
        "ON account_artifacts (account_id, hydration_status, size_bytes, updated_at DESC)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bin_items_expires_at ON bin_items (expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bin_items_account_deleted_at ON bin_items (account_id, deleted_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bin_items_account_expires_at ON bin_items (account_id, expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_account_created_at ON chat_sessions (account_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_account_updated_at ON chat_sessions (account_id, updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reward_sessions_account_created_at ON reward_sessions (account_id, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reward_sessions_account_created_at")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_account_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_account_created_at")
    op.execute("DROP INDEX IF EXISTS ix_bin_items_account_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_bin_items_account_deleted_at")
    op.execute("DROP INDEX IF EXISTS ix_bin_items_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_hydration_queue_ordered")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_hydration_queue")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_account_expires")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_account_deleted")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_account_artifacts_deleted_at")
    op.execute("DROP INDEX IF EXISTS ix_account_unlock_sessions_account_created_at")
    op.execute("DROP INDEX IF EXISTS ix_account_unlock_sessions_account_expires_created")
    op.execute("DROP INDEX IF EXISTS ix_account_unlock_sessions_expires_at")
