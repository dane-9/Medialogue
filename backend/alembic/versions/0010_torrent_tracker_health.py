"""Persist redacted per-torrent tracker health.

Revision ID: 0010
Revises: 0009
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    if "torrent_tracker_observations" in set(inspector.get_table_names()):
        return
    op.create_table(
        "torrent_tracker_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("torrent_id", sa.Uuid(), nullable=False),
        sa.Column("download_client_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_key", sa.String(length=64), nullable=False),
        sa.Column("endpoint_display", sa.Text(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("health_state", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("message_key", sa.String(length=64), nullable=True),
        sa.Column("peers", sa.Integer(), nullable=True),
        sa.Column("seeds", sa.Integer(), nullable=True),
        sa.Column("leeches", sa.Integer(), nullable=True),
        sa.Column("downloaded", sa.Integer(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("terminal_failure_count", sa.Integer(), nullable=False),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
        sa.Column("terminal_confirmed", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["torrent_id"], ["torrents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("torrent_id", "download_client_id", "endpoint_key", name="uq_torrent_tracker_endpoint"),
    )
    op.create_index(
        "ix_torrent_tracker_active_state",
        "torrent_tracker_observations",
        ["torrent_id", "active", "health_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_torrent_tracker_active_state", table_name="torrent_tracker_observations")
    op.drop_table("torrent_tracker_observations")
