"""Add removable failed-release blocklist.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The squashed 0001 baseline imports current metadata, so clean databases
    # already contain this table before later revisions run.
    op.create_table(
        "release_blocklist",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_type", sa.String(length=6), nullable=False),
        sa.Column("target_entity_type", sa.String(length=64), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column("torrent_id", sa.Uuid(), nullable=True),
        sa.Column("info_hash", sa.String(length=128), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("indexer_id", sa.Uuid(), nullable=True),
        sa.Column("indexer_name", sa.String(length=256), nullable=True),
        sa.Column("quality", sa.String(length=256), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("failure_event_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["failure_event_id"], ["events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["indexer_id"], ["indexers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["torrent_id"], ["torrents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index("ix_release_blocklist_created", "release_blocklist", ["created_at"], if_not_exists=True)
    op.create_index("ix_release_blocklist_target", "release_blocklist", ["target_entity_type", "target_entity_id"], if_not_exists=True)
    op.create_index("ix_release_blocklist_hash", "release_blocklist", ["info_hash"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_release_blocklist_hash", table_name="release_blocklist", if_exists=True)
    op.drop_index("ix_release_blocklist_target", table_name="release_blocklist", if_exists=True)
    op.drop_index("ix_release_blocklist_created", table_name="release_blocklist", if_exists=True)
    op.drop_table("release_blocklist", if_exists=True)
