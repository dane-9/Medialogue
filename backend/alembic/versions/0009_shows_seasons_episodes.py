"""Add optimistic revisions for Show hierarchy editing."""

from alembic import context, op
import sqlalchemy as sa

revision = "0009_shows_seasons_episodes"
down_revision = "0008_quality_profiles"
branch_labels = None
depends_on = None


def _add_if_missing(table: str, column: str) -> None:
    if context.is_offline_mode():
        op.add_column(table, sa.Column(column, sa.Integer(), nullable=False, server_default="1"))
        return
    inspector = sa.inspect(op.get_bind())
    if column not in {item["name"] for item in inspector.get_columns(table)}:
        op.add_column(table, sa.Column(column, sa.Integer(), nullable=False, server_default="1"))


def upgrade() -> None:
    _add_if_missing("shows", "revision")
    _add_if_missing("seasons", "revision")
    _add_if_missing("episodes", "revision")
    if context.is_offline_mode():
        op.add_column("media_files", sa.Column("last_exists_check_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("media_files", sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True))
        op.add_column("media_files", sa.Column("missing_check_count", sa.Integer(), nullable=False, server_default="0"))
    else:
        inspector = sa.inspect(op.get_bind())
        columns = {item["name"] for item in inspector.get_columns("media_files")}
        if "last_exists_check_at" not in columns:
            op.add_column("media_files", sa.Column("last_exists_check_at", sa.DateTime(timezone=True), nullable=True))
        if "missing_since" not in columns:
            op.add_column("media_files", sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True))
        if "missing_check_count" not in columns:
            op.add_column("media_files", sa.Column("missing_check_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    return
