"""add account key envelope table

Revision ID: 20260601_03
Revises: 20260601_02
Create Date: 2026-06-01 20:25:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260601_03"
down_revision = "20260601_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_keys",
        sa.Column("account_id", sa.String(length=64), primary_key=True),
        sa.Column("salt_b64", sa.String(length=256), nullable=False),
        sa.Column("argon2_params_json", sa.Text(), nullable=False),
        sa.Column("wrapped_dek_b64", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("account_keys")
