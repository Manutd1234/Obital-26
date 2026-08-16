"""add hydration status to account artifacts

Revision ID: 20260612_03
Revises: 20260612_02
Create Date: 2026-06-12 23:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260612_03"
down_revision = "20260612_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("account_artifacts")}
    if "hydration_status" not in columns:
        op.add_column(
            "account_artifacts",
            sa.Column("hydration_status", sa.String(length=24), nullable=False, server_default="full"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("account_artifacts")}
    if "hydration_status" in columns:
        op.drop_column("account_artifacts", "hydration_status")
