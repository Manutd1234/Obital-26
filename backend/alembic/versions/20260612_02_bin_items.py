"""add bin_items table and soft-delete columns on account_artifacts

Revision ID: 20260612_02
Revises: 20260612_01
Create Date: 2026-06-12 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260612_02"
down_revision = "20260612_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.func.now()

    op.execute(
        "CREATE TABLE IF NOT EXISTS bin_items ("
        "  id VARCHAR(64) NOT NULL,"
        "  account_id VARCHAR(64) NOT NULL,"
        "  item_type VARCHAR(32) NOT NULL,"
        "  item_id VARCHAR(256) NOT NULL,"
        "  name VARCHAR(512) NOT NULL,"
        "  metadata_json TEXT DEFAULT '{}' NOT NULL,"
        "  deleted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,"
        "  expires_at TIMESTAMP WITH TIME ZONE NOT NULL,"
        "  PRIMARY KEY (id)"
        ")"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bin_items_account_id ON bin_items (account_id)")

    op.execute("ALTER TABLE account_artifacts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE account_artifacts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")


def downgrade() -> None:
    op.drop_table("bin_items")
    op.drop_column("account_artifacts", "deleted_at")
    op.drop_column("account_artifacts", "expires_at")
