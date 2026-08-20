"""Persist qBittorrent health diagnostics separately from reconciliation state."""

from alembic import context, op
import sqlalchemy as sa

revision = "0011_download_client_health_diagnostics"
down_revision = "0010_torrent_size_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("download_clients", sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("download_clients", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("download_clients", sa.Column("latency_ms", sa.Integer(), nullable=True))
        op.add_column("download_clients", sa.Column("last_error", sa.Text(), nullable=True))
        return

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("download_clients") as batch_op:
            batch_op.add_column(sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("latency_ms", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        return

    op.add_column("download_clients", sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("download_clients", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("download_clients", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("download_clients", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_column("download_clients", "last_error")
        op.drop_column("download_clients", "latency_ms")
        op.drop_column("download_clients", "last_success_at")
        op.drop_column("download_clients", "last_health_checked_at")
        return

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("download_clients") as batch_op:
            batch_op.drop_column("last_error")
            batch_op.drop_column("latency_ms")
            batch_op.drop_column("last_success_at")
            batch_op.drop_column("last_health_checked_at")
        return

    op.drop_column("download_clients", "last_error")
    op.drop_column("download_clients", "latency_ms")
    op.drop_column("download_clients", "last_success_at")
    op.drop_column("download_clients", "last_health_checked_at")
