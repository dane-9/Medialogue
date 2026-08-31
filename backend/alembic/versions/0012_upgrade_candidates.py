"""Persist best known media upgrade candidates.

Revision ID: 0012
Revises: 0011
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    if "upgrade_candidates" in set(inspector.get_table_names()):
        return
    op.create_table(
        "upgrade_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_key", sa.String(length=160), nullable=False),
        sa.Column("media_type", sa.String(length=6), nullable=False),
        sa.Column("movie_id", sa.Uuid(), nullable=True),
        sa.Column("season_id", sa.Uuid(), nullable=True),
        sa.Column("current_release_ids", sa.JSON(), nullable=False),
        sa.Column("current_release_name", sa.Text(), nullable=False),
        sa.Column("current_quality", sa.String(length=256), nullable=True),
        sa.Column("current_custom_format_score", sa.Integer(), nullable=False),
        sa.Column("indexer_id", sa.Uuid(), nullable=True),
        sa.Column("indexer_name", sa.String(length=256), nullable=False),
        sa.Column("guid", sa.Text(), nullable=False),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("quality", sa.String(length=256), nullable=True),
        sa.Column("custom_format_score", sa.Integer(), nullable=False),
        sa.Column("score_delta", sa.Integer(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("seeders", sa.Integer(), nullable=True),
        sa.Column("leechers", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upgrade_types", sa.JSON(), nullable=False),
        sa.Column("added_custom_formats", sa.JSON(), nullable=False),
        sa.Column("execution_eligible", sa.Boolean(), nullable=False),
        sa.Column("execution_block_reason", sa.Text(), nullable=True),
        sa.Column("comparison_snapshot", sa.JSON(), nullable=False),
        sa.Column("last_searched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["indexer_id"], ["indexers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_key", name="uq_upgrade_candidates_target_key"),
    )
    op.create_index("ix_upgrade_candidates_media_type", "upgrade_candidates", ["media_type"])
    op.create_index("ix_upgrade_candidates_execution", "upgrade_candidates", ["execution_eligible", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_upgrade_candidates_execution", table_name="upgrade_candidates")
    op.drop_index("ix_upgrade_candidates_media_type", table_name="upgrade_candidates")
    op.drop_table("upgrade_candidates")
