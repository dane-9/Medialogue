"""Rows left out of New Media, Upgrade and Replace Media, kept on the server.

Revision ID: 0002
Revises: 0001

0001 creates the schema from the current models, so a fresh database already
has this table; only databases created before it need it added. IF NOT EXISTS
covers both, online and in offline SQL generation (Medialogue is SQLite-only).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS ui_selections ("
        "list_key VARCHAR(64) NOT NULL, "
        "item_id VARCHAR(256) NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "PRIMARY KEY (list_key, item_id))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ui_selections")
