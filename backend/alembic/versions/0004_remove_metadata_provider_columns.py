"""Remove columns left by the retired Plex metadata-provider experiment.

Revision ID: 0004
Revises: 0003
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


_REMOVED_COLUMNS = {
    "movies": ("metadata_provider", "metadata_provider_id", "plex_guid"),
    "shows": ("metadata_provider", "metadata_provider_id", "plex_guid"),
    "media_discoveries": ("metadata_provider", "suggested_provider_id", "selected_provider_id"),
}


def upgrade() -> None:
    if context.is_offline_mode():
        for table_name, columns in _REMOVED_COLUMNS.items():
            for column_name in columns:
                op.drop_column(table_name, column_name)
        return

    inspector = sa.inspect(op.get_bind())
    for table_name, columns in _REMOVED_COLUMNS.items():
        if table_name not in inspector.get_table_names():
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        removable = [column_name for column_name in columns if column_name in existing]
        if not removable:
            continue
        with op.batch_alter_table(table_name) as batch:
            for column_name in removable:
                batch.drop_column(column_name)


def downgrade() -> None:
    for table_name, columns in _REMOVED_COLUMNS.items():
        with op.batch_alter_table(table_name) as batch:
            for column_name in columns:
                batch.add_column(sa.Column(column_name, sa.String(length=512), nullable=True))
