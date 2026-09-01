"""Persist selected tracker titles for current release display.

Revision ID: 0014
Revises: 0013
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def upgrade() -> None:
    if context.is_offline_mode():
        return
    _add_column_if_missing("movie_releases", sa.Column("tracker_title", sa.Text(), nullable=True))
    _add_column_if_missing("show_releases", sa.Column("tracker_title", sa.Text(), nullable=True))
    _add_column_if_missing("search_results", sa.Column("submitted_info_hash", sa.String(length=64), nullable=True))
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("search_results")}
    if "ix_search_results_submitted_info_hash" not in indexes:
        op.create_index(
            "ix_search_results_submitted_info_hash",
            "search_results",
            ["submitted_info_hash"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_search_results_submitted_info_hash", table_name="search_results")
    op.drop_column("search_results", "submitted_info_hash")
    op.drop_column("show_releases", "tracker_title")
    op.drop_column("movie_releases", "tracker_title")
