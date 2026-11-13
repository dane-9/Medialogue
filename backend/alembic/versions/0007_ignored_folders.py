"""Folders that scans pass over.

Revision ID: 0007
Revises: 0006

Removing a title from Medialogue while keeping its files records the folder
here, so a scan does not add it straight back. 0001 creates the schema from
the current models, so a fresh database already has this table; IF NOT EXISTS
covers both.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS ignored_folders ("
        "id CHAR(32) NOT NULL, "
        "resolved_path TEXT NOT NULL, "
        "media_type VARCHAR(6) NOT NULL, "
        "title VARCHAR(512) NOT NULL, "
        "year INTEGER, "
        "created_at DATETIME NOT NULL, "
        "PRIMARY KEY (id), "
        "UNIQUE (resolved_path))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ignored_folders")
