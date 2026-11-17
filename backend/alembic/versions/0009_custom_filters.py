"""Saved filters for the Movies and Shows lists.

Revision ID: 0009
Revises: 0008

0001 creates the schema from the current models, so a fresh database already
has this table; IF NOT EXISTS covers both.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS custom_filters ("
        "id CHAR(32) NOT NULL, "
        "media_type VARCHAR(6) NOT NULL, "
        "label VARCHAR(128) NOT NULL, "
        "filters JSON NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (id))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_custom_filters_media_type ON custom_filters (media_type)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_custom_filters_media_type")
    op.execute("DROP TABLE IF EXISTS custom_filters")
