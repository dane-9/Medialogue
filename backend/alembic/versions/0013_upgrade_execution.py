"""Track explicit Upgrade Media execution and cleanup state.

Revision ID: 0013
Revises: 0012
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("upgrade_candidates")}
    additions = [
        ("execution_state", sa.String(length=40)),
        ("submitted_info_hash", sa.String(length=64)),
        ("selected_download_client_id", sa.Uuid()),
        ("selected_download_client_name", sa.String(length=256)),
        ("selected_category", sa.String(length=256)),
        ("submitted_at", sa.DateTime(timezone=True)),
        ("torrent_id", sa.Uuid()),
        ("incoming_release_ids", sa.JSON()),
        ("validated_at", sa.DateTime(timezone=True)),
        ("old_torrents_removed_at", sa.DateTime(timezone=True)),
        ("cleanup_completed_at", sa.DateTime(timezone=True)),
        ("execution_error", sa.Text()),
    ]
    for name, type_ in additions:
        if name not in columns:
            op.add_column("upgrade_candidates", sa.Column(name, type_, nullable=True))
    op.execute("UPDATE upgrade_candidates SET incoming_release_ids = '[]' WHERE incoming_release_ids IS NULL")


def downgrade() -> None:
    for name in [
        "execution_error",
        "cleanup_completed_at",
        "old_torrents_removed_at",
        "validated_at",
        "incoming_release_ids",
        "torrent_id",
        "submitted_at",
        "selected_category",
        "selected_download_client_name",
        "selected_download_client_id",
        "submitted_info_hash",
        "execution_state",
    ]:
        op.drop_column("upgrade_candidates", name)
