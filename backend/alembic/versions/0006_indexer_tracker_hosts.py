"""Which tracker hosts each indexer's torrents announce to.

Revision ID: 0006
Revises: 0005

Lets a torrent Medialogue did not grab itself be traced to an indexer. 0001
creates the schema from the current models, so a fresh database already has
this table; IF NOT EXISTS covers both.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS indexer_tracker_hosts ("
        "indexer_id CHAR(32) NOT NULL, "
        "host VARCHAR(255) NOT NULL, "
        "learned_at DATETIME NOT NULL, "
        "PRIMARY KEY (indexer_id, host))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS indexer_tracker_hosts")
