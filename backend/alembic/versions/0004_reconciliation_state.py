"""Persist configurable missing grace and observation counters."""

from alembic import context, op
import sqlalchemy as sa


revision = "0004_reconciliation_state"
down_revision = "0003_download_client_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute("ALTER TABLE movies ADD COLUMN IF NOT EXISTS revision INTEGER DEFAULT 1 NOT NULL")
        op.execute("ALTER TABLE storage_roots ADD COLUMN IF NOT EXISTS missing_grace_checks INTEGER DEFAULT 2 NOT NULL")
        op.execute("ALTER TABLE media_directories ADD COLUMN IF NOT EXISTS missing_check_count INTEGER DEFAULT 0 NOT NULL")
        return
    bind = op.get_bind()
    movie_columns = {item["name"] for item in sa.inspect(bind).get_columns("movies")}
    if "revision" not in movie_columns:
        op.add_column("movies", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    root_columns = {item["name"] for item in sa.inspect(bind).get_columns("storage_roots")}
    if "missing_grace_checks" not in root_columns:
        op.add_column(
            "storage_roots",
            sa.Column("missing_grace_checks", sa.Integer(), nullable=False, server_default="2"),
        )
    directory_columns = {item["name"] for item in sa.inspect(bind).get_columns("media_directories")}
    if "missing_check_count" not in directory_columns:
        op.add_column(
            "media_directories",
            sa.Column("missing_check_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    # Forward-only compatibility revision. These columns are part of the
    # explicit 0001 baseline for fresh installations.
    return
