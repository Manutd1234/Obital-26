"""create account_artifacts and account_registry_records tables

Revision ID: 20260601_04
Revises: 20260601_03
Create Date: 2026-06-01 21:00:00
"""

from alembic import op

revision = "20260601_04"
down_revision = "20260601_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS account_artifacts ("
        "  account_id VARCHAR(64) NOT NULL,"
        "  relative_path VARCHAR(1024) NOT NULL,"
        "  content BYTEA NOT NULL,"
        "  sha256 VARCHAR(64) NOT NULL,"
        "  size_bytes INTEGER NOT NULL,"
        "  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,"
        "  deleted_at TIMESTAMP WITH TIME ZONE,"
        "  expires_at TIMESTAMP WITH TIME ZONE,"
        "  hydration_status VARCHAR(24) DEFAULT 'full' NOT NULL,"
        "  PRIMARY KEY (account_id, relative_path)"
        ")"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS account_registry_records ("
        "  account_id VARCHAR(64) NOT NULL,"
        "  profile_json TEXT NOT NULL,"
        "  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,"
        "  PRIMARY KEY (account_id)"
        ")"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_artifacts_sha256 ON account_artifacts (sha256)")


def downgrade() -> None:
    op.drop_table("account_registry_records")
    op.drop_table("account_artifacts")
