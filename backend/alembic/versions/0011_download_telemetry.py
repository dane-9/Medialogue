"""Persist live telemetry per qBittorrent client observation.

Revision ID: 0011
Revises: 0010
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


_COLUMNS = (
    sa.Column("download_speed", sa.BigInteger(), nullable=True),
    sa.Column("upload_speed", sa.BigInteger(), nullable=True),
    sa.Column("eta_seconds", sa.Integer(), nullable=True),
    sa.Column("amount_left", sa.BigInteger(), nullable=True),
    sa.Column("ratio", sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column("seeds", sa.Integer(), nullable=True),
    sa.Column("leeches", sa.Integer(), nullable=True),
    sa.Column("availability", sa.Numeric(precision=12, scale=4), nullable=True),
)


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    existing = {
        column["name"]
        for column in inspector.get_columns("torrent_client_observations")
    }
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("torrent_client_observations", column)


def downgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    existing = {
        column["name"]
        for column in inspector.get_columns("torrent_client_observations")
    }
    with op.batch_alter_table("torrent_client_observations") as batch_op:
        for column in reversed(_COLUMNS):
            if column.name in existing:
                batch_op.drop_column(column.name)
