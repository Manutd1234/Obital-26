"""add shared rate limit buckets

Revision ID: 20260628_01
Revises: 20260623_01
Create Date: 2026-06-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260628_01"
down_revision = "20260623_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=160), primary_key=True),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_limit_buckets_updated_at", "rate_limit_buckets", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_buckets_updated_at", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
