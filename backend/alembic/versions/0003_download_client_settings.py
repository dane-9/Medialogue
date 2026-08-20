"""Persist qBittorrent client revisions and polling preferences."""

from alembic import context, op
import sqlalchemy as sa


revision = "0003_download_client_settings"
down_revision = "0002_plex_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 intentionally creates the model metadata for a clean install.  A
    # fresh database therefore already has these columns, while an older
    # installation may need them added.  Inspect first so both histories are
    # valid (notably SQLite cannot add an existing column).
    if context.is_offline_mode():
        op.execute("ALTER TABLE download_clients ADD COLUMN IF NOT EXISTS revision INTEGER DEFAULT 1 NOT NULL")
        op.execute("ALTER TABLE download_clients ADD COLUMN IF NOT EXISTS poll_interval_seconds INTEGER DEFAULT 15 NOT NULL")
        op.execute("ALTER TABLE download_clients ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMP WITH TIME ZONE")
        return
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("download_clients")}
    if "revision" not in columns:
        op.add_column("download_clients", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    if "poll_interval_seconds" not in columns:
        op.add_column("download_clients", sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="15"))
    if "last_polled_at" not in columns:
        op.add_column("download_clients", sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True))
    # Keep the server defaults as a safe backfill for existing installations;
    # the ORM also supplies defaults for newly created rows.  Avoid dropping
    # them here because SQLite cannot ALTER COLUMN and remains useful for
    # local development/test migrations.


def downgrade() -> None:
    # Forward-only compatibility revision. These columns are part of the
    # explicit 0001 baseline for fresh installations.
    return
