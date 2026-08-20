"""Store torrent byte sizes as 64-bit integers.

qBittorrent reports total_size in bytes.  Real movie/season torrents routinely
exceed PostgreSQL INTEGER's 2,147,483,647-byte limit, so this column must be a
BIGINT.
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0010_torrent_size_bigint"
down_revision = "0009_shows_seasons_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.alter_column(
            "torrents",
            "total_size",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )
        return

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("torrents") as batch_op:
            batch_op.alter_column(
                "total_size",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=True,
            )
        return

    op.alter_column(
        "torrents",
        "total_size",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Deliberately do not narrow the column again. Once a database has observed
    # a >2 GiB torrent, converting back to INTEGER can destroy the upgrade path.
    return
