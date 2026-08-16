"""compat marker for reverted chunk_count migration

Revision ID: 20260623_01
Revises: 20260617_01
Create Date: 2026-06-23
"""

from alembic import op


revision = "20260623_01"
down_revision = "20260617_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This revision was previously applied from a local, later-reverted
    # chunk_count migration. Keep the id so existing databases can be opened
    # by this known-good checkout.
    pass


def downgrade() -> None:
    pass
