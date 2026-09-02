"""Retain complete per-target upgrade search result sets.

Revision ID: 0015
Revises: 0014
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("upgrade_candidates")}
    if "search_results" not in columns:
        op.add_column(
            "upgrade_candidates",
            sa.Column("search_results", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_column("upgrade_candidates", "search_results")
